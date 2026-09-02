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

local function send(method, params, callback)
  state.next_id = state.next_id + 1
  local id = state.next_id
  if callback then
    state.pending[id] = callback
  end
  local message = { jsonrpc = '2.0', id = id, method = method }
  if params then
    message.params = params
  end
  vim.fn.chansend(state.job, vim.json.encode(message) .. '\n')
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
    vim.fn.chansend(
      state.job,
      vim.json.encode({ jsonrpc = '2.0', method = 'notifications/initialized' }) .. '\n'
    )
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

--- Ask, and wait briefly for the answer.
--- fzf-lua wants results back from its callback, so block for the round
--- trip rather than restructure the picker around a callback.
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
