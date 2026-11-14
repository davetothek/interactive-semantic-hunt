-- lua/utils/ish_ask.lua
-- Ask ish a question, and read the answer in a split.
--
-- The answer comes from `ish ask`, which writes Markdown on stdout
-- with each source as a link. Running the command line keeps one set of
-- settings: the language model and the filters are the ones a shell
-- uses, so an answer here is the answer there.
--
-- The split opens at once and names the question, because an answer
-- takes seconds and a key that shows nothing looks dead. The command
-- runs in the background, so Neovim keeps drawing while it waits.
--
-- In the answer, `link_keys` open the source the cursor points at in the
-- window the question came from, so the answer stays in view. They are
-- set on the buffer, where no global mapping of the same key reaches.
--
--   vim.keymap.set('n', '<leader>fa', function() require('utils.ish_ask').ask() end,
--     { desc = 'Ask ish a question' })

local M = {}

-- The command that opens the window of an answer, such as 'split',
-- 'vsplit', or 'botright 15split'.
M.split = 'vsplit'

-- Keys that open the source under the cursor, in the answer only.
M.link_keys = { 'gf' }

-- How often the waiting line counts the seconds, in milliseconds.
local TICK_MS = 1000

-- Exposed for testing the pieces this module builds.
local _internal = {}

local function root()
  local found = vim.fs.find('.git', { upward = true, path = vim.uv.cwd() })[1]
  return found and vim.fs.dirname(found) or vim.uv.cwd()
end

-- Build the command that asks *question* about the tree at *cwd*. The
-- question follows `--`, so one that starts with a hyphen is not read
-- as a flag.
local function command(question, opts)
  local parts = { 'ish', 'ask' }
  vim.list_extend(parts, opts.args or {})
  vim.list_extend(parts, { '--', question, opts.cwd or root() })
  return parts
end
_internal.command = command

-- Build the command that asks the server. A server answers from its own
-- tree, so the command names no path.
local function remote_command(question, opts)
  local parts = { 'ish', 'remote', 'ask' }
  vim.list_extend(parts, opts.args or {})
  vim.list_extend(parts, { '--', question })
  return parts
end
_internal.remote_command = remote_command

-- The exit code of an answer that the documents do not cover.
local NOT_COVERED = 2

-- Defined below `M.ask`, which uses it.
local answer

local function lines_of(text)
  return vim.split(vim.trim(text), '\n', { plain = true })
end

-- Return the text a buffer shows for a run that gave no answer: the
-- question, the exit code, and what ish wrote on stderr.
local function failure(question, code, stderr)
  local lines = { '# ' .. question, '', ('ish gave no answer (exit code %d).'):format(code) }
  local said = vim.trim(stderr or '')
  if said ~= '' then
    vim.list_extend(lines, { '', '```text' })
    vim.list_extend(lines, lines_of(said))
    table.insert(lines, '```')
  end
  return lines
end
_internal.failure = failure

local function fill(buffer, lines)
  vim.bo[buffer].modifiable = true
  vim.api.nvim_buf_set_lines(buffer, 0, -1, false, lines)
  vim.bo[buffer].modifiable = false
end

-- Open the source the cursor points at in the window the question came
-- from. When that window is gone, make one, so the answer stays in view.
local function open_source()
  local links = require('utils.ish_link')
  local link = links.link()
  if not link then
    vim.notify('ish: no link under the cursor', vim.log.levels.WARN)
    return
  end
  local answer = vim.api.nvim_get_current_win()
  vim.cmd('wincmd p')
  if vim.api.nvim_get_current_win() == answer then
    vim.cmd('aboveleft vsplit')
  end
  links.open(link)
end

-- Open the window of an answer on a buffer that holds no file, and
-- return the buffer.
local function open(question)
  local buffer = vim.api.nvim_create_buf(false, true)
  vim.cmd(M.split)
  vim.api.nvim_win_set_buf(0, buffer)
  vim.bo[buffer].bufhidden = 'wipe'
  vim.bo[buffer].filetype = 'markdown'
  vim.b[buffer].ish_question = question
  -- After the filetype, so these keys win over the ones a ftplugin sets.
  for _, key in ipairs(M.link_keys) do
    vim.keymap.set('n', key, open_source, { buffer = buffer, desc = 'Open the source under the cursor' })
  end
  return buffer
end

--- Ask *question* and show the answer in a split. With no question, ask
--- for one first.
--- @param question string|nil
--- @param opts table|nil  cwd: the tree to ask about, the repository by
---   default. args: more flags for ish, such as { '--brief' }.
function M.ask(question, opts)
  opts = opts or {}
  question = question and vim.trim(question) or ''
  if question == '' then
    vim.ui.input({ prompt = 'Ask ish: ' }, function(typed)
      if typed and vim.trim(typed) ~= '' then
        M.ask(typed, opts)
      end
    end)
    return
  end
  if vim.fn.executable('ish') ~= 1 then
    vim.notify('ish is not on PATH', vim.log.levels.WARN)
    return
  end

  opts.cwd = opts.cwd or root()
  -- Ask this tree, the server, or this tree and then the server. With no
  -- index and no server, the choice asks before it builds an index.
  require('utils.ish_server').choose(opts.cwd, opts.source, function(choice)
    if not choice then
      return
    end
    if not choice.use_local then
      return answer(question, remote_command(question, opts))
    end
    answer(question, command(question, opts), function(code)
      -- The tree here does not cover it. The server may, so offer it.
      if code == NOT_COVERED and choice.use_remote then
        local asked = vim.fn.confirm('This tree does not cover it. Ask the server?', '&Ask\n&No', 1)
        if asked == 1 then
          answer(question, remote_command(question, opts))
        end
      end
    end)
  end)
end

-- Run *argv* for *question*, show the answer in a split, and call
-- *after* with the exit code once it is shown.
answer = function(question, argv, after)
  local buffer = open(question)
  local started = vim.uv.now()
  local function waiting()
    if vim.api.nvim_buf_is_valid(buffer) then
      local seconds = math.floor((vim.uv.now() - started) / 1000)
      fill(buffer, { '# ' .. question, '', ('Waiting for the answer, %d s.'):format(seconds) })
    end
  end
  waiting()
  local timer = vim.uv.new_timer()
  timer:start(TICK_MS, TICK_MS, vim.schedule_wrap(waiting))

  vim.system(argv, { text = true }, function(result)
    vim.schedule(function()
      timer:stop()
      timer:close()
      -- A buffer closed while the question ran wants no answer.
      if not vim.api.nvim_buf_is_valid(buffer) then
        return
      end
      -- An answer that the documents do not cover exits with 2 and still
      -- prints the closest sources, so judge by the text, not the code.
      local text = vim.trim(result.stdout or '')
      if text == '' then
        fill(buffer, failure(question, result.code, result.stderr))
      else
        fill(buffer, lines_of(text))
      end
      if after then
        after(result.code)
      end
    end)
  end)
end

M._internal = _internal

return M
