-- lua/utils/ish_server.lua
-- One resident ish-mcp process per Neovim session, and one for a server.
--
-- Spawning ish for every keystroke pays interpreter and library startup
-- each time, which costs about half a second. A resident server pays it
-- once and answers in about a fifth of that.
--
-- Starting is idempotent: the server is spawned on first use and reused
-- afterwards, so nothing runs until something asks a question.
--
-- The functions of this module act on the server of the local tree,
-- `ish-mcp`. `M.remote` is a second one, `ish remote mcp`, for the tree
-- of the server that `ISH_REMOTE` or the user config names. It starts
-- only when a server is set, so `M.available` tells whether there is one.

local M = {}

-- Make a client of the server that *argv* starts. With *paths* false, a
-- call names no path, because a server searches its own tree.
local function new(argv, paths)
  local server = {
    argv = argv,
    paths = paths,
    job = nil,
    next_id = 0,
    pending = {},
    buffer = '',
    ready = false,
    -- The last line the process wrote on stderr, to say why it stopped.
    last_error = '',
    -- Callbacks that wait for `ready`, or for the process to stop.
    waiting = {},
  }

  local function handle(line)
    if line == '' then
      return
    end
    local ok, message = pcall(vim.json.decode, line)
    if not ok or type(message) ~= 'table' or message.id == nil then
      return
    end
    local callback = server.pending[message.id]
    if not callback then
      return
    end
    server.pending[message.id] = nil
    callback(message.result, message.error)
  end

  local function on_stdout(_, data)
    if not data or #data == 0 then
      return
    end
    -- Neovim splits a chunk on newlines: the first element continues the
    -- previous partial line, and every element after it starts a new one.
    server.buffer = server.buffer .. data[1]
    for index = 2, #data do
      handle(server.buffer)
      server.buffer = data[index]
    end
  end

  local function on_stderr(_, data)
    for _, line in ipairs(data or {}) do
      if vim.trim(line) ~= '' then
        -- Drop the time and the level that the log of ish starts with.
        server.last_error = (vim.trim(line):gsub('^.-%[%u+%]%s*', ''))
      end
    end
  end

  local function settle(up)
    local waiting = server.waiting
    server.waiting = {}
    for _, callback in ipairs(waiting) do
      pcall(callback, up)
    end
  end

  -- Write one message, unless the server has gone. A job that exits
  -- leaves callbacks in flight, and writing to it raises.
  local function write(message)
    if not server.job then
      return false
    end
    local ok = pcall(vim.fn.chansend, server.job, vim.json.encode(message) .. '\n')
    if not ok then
      server.job = nil
    end
    return ok
  end

  local function send(method, params, callback)
    server.next_id = server.next_id + 1
    local id = server.next_id
    local message = { jsonrpc = '2.0', id = id, method = method }
    if params then
      message.params = params
    end
    if not write(message) then
      -- Nothing will answer, so let the caller stop waiting.
      if callback then
        callback(nil, 'the server has gone')
      end
      return
    end
    if callback then
      server.pending[id] = callback
    end
  end

  --- Report whether the server is up, without starting it.
  function server.running()
    return server.job ~= nil and vim.fn.jobwait({ server.job }, 0)[1] == -1
  end

  --- Start the server if it is not already running. Safe to call repeatedly.
  function server.ensure()
    if server.job and vim.fn.jobwait({ server.job }, 0)[1] == -1 then
      return true
    end

    server.job, server.ready, server.pending, server.buffer = nil, false, {}, ''
    if vim.fn.executable(argv[1]) ~= 1 then
      server.last_error = argv[1] .. ' is not on PATH'
      vim.notify(server.last_error, vim.log.levels.WARN)
      settle(false)
      return false
    end

    server.job = vim.fn.jobstart(argv, {
      on_stdout = on_stdout,
      on_stderr = on_stderr,
      on_exit = function()
        server.job, server.ready = nil, false
        local in_flight = server.pending
        server.pending = {}
        for _, callback in pairs(in_flight) do
          pcall(callback, nil, 'the server stopped')
        end
        settle(false)
      end,
      stderr_buffered = false,
    })
    if server.job <= 0 then
      server.last_error = 'could not start ' .. table.concat(argv, ' ')
      vim.notify(server.last_error, vim.log.levels.ERROR)
      server.job = nil
      settle(false)
      return false
    end

    send('initialize', { protocolVersion = '2025-06-18', capabilities = {} }, function(result)
      if not result then
        return
      end
      server.ready = true
      write({ jsonrpc = '2.0', method = 'notifications/initialized' })
      settle(true)
    end)
    return true
  end

  --- Call back with true once the server answers, or false when it stops
  --- first. `ish remote mcp` stops at once when no server is set.
  function server.available(callback)
    if server.ready and server.running() then
      return callback(true)
    end
    table.insert(server.waiting, callback)
    if not server.ensure() then
      settle(false)
    end
  end

  -- The arguments of a call, with the path only where the server takes one.
  local function located(arguments, path)
    if server.paths and path then
      arguments.path = path
    end
    return arguments
  end

  --- Ask the server for results. Calls back with a list of grep-shaped lines.
  function server.search(query, opts, callback)
    if not server.ensure() then
      return callback({})
    end

    local arguments = located({
      query = query,
      limit = opts.limit or 40,
      format = 'grep',
    }, opts.cwd)
    if opts.lang and #opts.lang > 0 then
      arguments.lang = opts.lang
    end
    if opts.under and opts.under ~= '' then
      arguments.under = opts.under
    end
    if opts.type and #opts.type > 0 then
      arguments.type = opts.type
    end

    send('tools/call', { name = 'search_code', arguments = arguments }, function(result, err)
      if err or not result or result.isError then
        return callback({})
      end
      local text = result.content and result.content[1] and result.content[1].text or ''
      local lines = {}
      for line in text:gmatch('[^\n]+') do
        -- "No results for ..." is prose, not a result.
        if line:match('^[^:]+:%d+:%d+:') then
          table.insert(lines, line)
        end
      end
      callback(lines)
    end)
  end

  --- Call one tool and hand the reply text to *callback*.
  function server.call(name, arguments, callback)
    if not server.ensure() then
      return callback(nil)
    end
    send('tools/call', { name = name, arguments = arguments }, function(result, err)
      if err or not result or result.isError then
        return callback(nil)
      end
      callback(result.content and result.content[1] and result.content[1].text or '')
    end)
  end

  --- Ask the server to bring the index up to date, and return at once.
  --- Searching keeps working while it runs, and answers improve as it goes.
  function server.refresh(path, callback)
    server.call('refresh_index', located({}, path), callback or function() end)
  end

  --- Report what the index holds, and whether a refresh is running.
  --- Calls back with { refreshing = string|nil, chunks = number|nil }.
  function server.status(path, callback)
    server.call('index_status', located({}, path), function(text)
      if not text then
        return callback({})
      end
      local doing = text:match('refreshing%s*:%s*([^\n]+)')
      if doing == 'no' then
        doing = nil
      end
      callback({ refreshing = doing, chunks = tonumber(text:match('chunks%s*:%s*(%d+)')) })
    end)
  end

  --- Call back with true when a stored index reaches *path*.
  --- Asking starts no index and no refresh.
  function server.exists(path, callback)
    server.call('index_exists', located({}, path), function(text)
      local ok, reply = pcall(vim.json.decode, text or '')
      callback(ok and type(reply) == 'table' and reply.exists == true)
    end)
  end

  --- Ask, and wait for the answer.
  ---
  --- This stops Neovim redrawing for the length of a round trip, so it
  --- must not be used where a person is typing: it cost 160 ms a
  --- keystroke in the picker, which is what made the field feel heavy.
  --- Use `search` there, which hands the answer back when it arrives.
  --- Kept for scripts and measurements, where blocking is what is wanted.
  --- @return string[] lines
  function server.search_now(query, opts)
    local answer, done = {}, false
    server.search(query, opts, function(lines)
      answer, done = lines, true
    end)
    vim.wait(opts.timeout or 4000, function()
      return done
    end, 10)
    return answer
  end

  --- Stop the server. Starting again is a call to ensure().
  function server.stop()
    if server.job then
      vim.fn.jobstop(server.job)
      server.job, server.ready = nil, false
    end
  end

  return server
end

M.new = new
M.local_server = new({ 'ish-mcp' }, true)
M.remote = new({ 'ish', 'remote', 'mcp' }, false)

-- The trees this session chose to search locally. A save refreshes only
-- these, so a tree the person chose not to index never gets one.
M.indexed = {}

-- The answer for each tree to the question whether to build its index.
-- A question at each search would soon be noise: after a yes the index is
-- under way, and a no stands until Neovim starts again.
M.build_answers = {}

--- Ask before an index is built, because a large tree takes hours.
--- @return boolean build
local function agree_to_build(cwd)
  local answer = vim.fn.confirm(
    ('ish has no index of %s. Build one now?\n'
      .. 'It embeds about one chunk a second, so a large tree takes hours.\n'
      .. 'ish asks once for each tree until Neovim starts again.'):format(cwd),
    '&Build\n&Not now',
    2
  )
  return answer == 1
end
M._agree_to_build = agree_to_build

-- The answer for *cwd*, asked the first time only.
local function agreed(cwd)
  if M.build_answers[cwd] == nil then
    M.build_answers[cwd] = M._agree_to_build(cwd)
  end
  return M.build_answers[cwd]
end

--- Choose where to search *cwd*, and call back with
--- { use_local = boolean, use_remote = boolean }, or nil for nowhere.
---
--- *mode* is `vim.g.ish_source` when nil: 'auto' (the default), 'local',
--- or 'remote'. With 'auto', search the local index when there is one,
--- the server when one is set, and both when both are. With neither,
--- or with 'local' and no index, ask before an index is built.
function M.choose(cwd, mode, callback)
  mode = mode or vim.g.ish_source or 'auto'
  local function chosen(use_local, use_remote)
    if use_local then
      M.indexed[cwd] = true
    end
    callback({ use_local = use_local, use_remote = use_remote })
  end
  local function build_or_stop()
    if agreed(cwd) then
      return chosen(true, false)
    end
    callback(nil)
  end

  if mode == 'remote' then
    return M.remote.available(function(up)
      if up then
        return chosen(false, true)
      end
      vim.notify('ish: ' .. (M.remote.last_error ~= '' and M.remote.last_error or 'no server'), vim.log.levels.WARN)
      callback(nil)
    end)
  end
  M.local_server.exists(cwd, function(has)
    if mode == 'local' then
      if has then
        return chosen(true, false)
      end
      return build_or_stop()
    end
    M.remote.available(function(up)
      if has or up then
        return chosen(has, up)
      end
      build_or_stop()
    end)
  end)
end

-- The functions of the module are those of the local server, so a caller
-- written for one server keeps working.
for _, name in ipairs({
  'running',
  'ensure',
  'available',
  'search',
  'call',
  'refresh',
  'status',
  'exists',
  'search_now',
  'stop',
}) do
  M[name] = M.local_server[name]
end

return M
