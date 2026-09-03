-- lua/utils/ish_server.lua
-- One resident ish-mcp process per Neovim session.
--
-- Spawning ish for every keystroke pays interpreter and library startup
-- each time, which costs about half a second. A resident server pays it
-- once and answers in about a fifth of that.
--
-- Starting is idempotent: the server is spawned on first use and reused
-- afterwards, so nothing runs until something asks a question.

local M = {}

local state = {
  job = nil,
  next_id = 0,
  pending = {},
  buffer = '',
  ready = false,
}

local function handle(line)
  if line == '' then
    return
  end
  local ok, message = pcall(vim.json.decode, line)
  if not ok or type(message) ~= 'table' or message.id == nil then
    return
  end
  local callback = state.pending[message.id]
  if not callback then
    return
  end
  state.pending[message.id] = nil
  callback(message.result, message.error)
end

local function on_stdout(_, data)
  if not data or #data == 0 then
    return
  end
  -- Neovim splits a chunk on newlines: the first element continues the
  -- previous partial line, and every element after it starts a new one.
  state.buffer = state.buffer .. data[1]
  for index = 2, #data do
    handle(state.buffer)
    state.buffer = data[index]
  end
end

-- Write one message, unless the server has gone. A job that exits
-- leaves callbacks in flight, and writing to it raises.
local function write(message)
  if not state.job then
    return false
  end
  local ok = pcall(vim.fn.chansend, state.job, vim.json.encode(message) .. '\n')
  if not ok then
    state.job = nil
  end
  return ok
end

local function send(method, params, callback)
  state.next_id = state.next_id + 1
  local id = state.next_id
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
    state.pending[id] = callback
  end
end

--- Start the server if it is not already running. Safe to call repeatedly.
--- @return boolean running
function M.ensure()
  if state.job and vim.fn.jobwait({ state.job }, 0)[1] == -1 then
    return true
  end

  state.job, state.ready, state.pending, state.buffer = nil, false, {}, ''
  if vim.fn.executable('ish-mcp') ~= 1 then
    vim.notify('ish-mcp is not on PATH', vim.log.levels.WARN)
    return false
  end

  state.job = vim.fn.jobstart({ 'ish-mcp' }, {
    on_stdout = on_stdout,
    on_exit = function()
      state.job, state.ready = nil, false
      local waiting = state.pending
      state.pending = {}
      for _, callback in pairs(waiting) do
        pcall(callback, nil, 'the server stopped')
      end
    end,
    stderr_buffered = false,
  })
  if state.job <= 0 then
    vim.notify('could not start ish-mcp', vim.log.levels.ERROR)
    state.job = nil
    return false
  end

  send('initialize', { protocolVersion = '2025-06-18', capabilities = {} }, function()
    state.ready = true
    write({ jsonrpc = '2.0', method = 'notifications/initialized' })
  end)
  return true
end

--- Ask the server for results. Calls back with a list of grep-shaped lines.
function M.search(query, opts, callback)
  if not M.ensure() then
    return callback({})
  end

  local arguments = {
    query = query,
    path = opts.cwd,
    limit = opts.limit or 40,
    format = 'grep',
  }
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
local function call(name, arguments, callback)
  if not M.ensure() then
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
function M.refresh(path, callback)
  call('refresh_index', { path = path }, callback or function() end)
end

--- Report what the index holds, and whether a refresh is running.
--- Calls back with { refreshing = string|nil, chunks = number|nil }.
function M.status(path, callback)
  call('index_status', { path = path }, function(text)
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

--- Ask, and wait for the answer.
---
--- This stops Neovim redrawing for the length of a round trip, so it
--- must not be used where a person is typing: it cost 160 ms a
--- keystroke in the picker, which is what made the field feel heavy.
--- Use `M.search` there, which hands the answer back when it arrives.
--- Kept for scripts and measurements, where blocking is what is wanted.
--- @return string[] lines
function M.search_now(query, opts)
  local answer, done = {}, false
  M.search(query, opts, function(lines)
    answer, done = lines, true
  end)
  vim.wait(opts.timeout or 4000, function()
    return done
  end, 10)
  return answer
end

--- Stop the server. Starting again is a call to ensure().
function M.stop()
  if state.job then
    vim.fn.jobstop(state.job)
    state.job, state.ready = nil, false
  end
end

return M
