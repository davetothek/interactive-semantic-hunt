-- lua/utils/ish.lua
-- Semantic code search backed by ish, presented through fzf-lua.
--
-- Each keystroke asks the resident ish server one question. The index is
-- already built, so a query costs a search rather than a scan, and
-- fzf-lua debounces the typing.

local M = {}

-- Exposed for testing the pieces this module builds.
local _internal = {}

-- A rank as it is printed at the head of a line, for example "0.71 ".
local RANK = '^%s*%d+%.%d+%s+'

local function root()
  local found = vim.fs.find('.git', { upward = true, path = vim.uv.cwd() })[1]
  return found and vim.fs.dirname(found) or vim.uv.cwd()
end

-- fzf-lua hands the typed query to a live callback as a string in some
-- versions and as a table in others. Take either.
local function query_text(query)
  if type(query) == 'table' then
    query = query[1]
  end
  return type(query) == 'string' and query or ''
end

-- Build the command that runs ish directly, for when the server cannot
-- be reached.
local function command(query, opts)
  local parts = {
    'ish',
    vim.fn.shellescape(query_text(query)),
    vim.fn.shellescape(opts.cwd or root()),
    '--format grep',
    '--limit ' .. (opts.limit or 40),
  }
  if opts.lang and #opts.lang > 0 then
    table.insert(parts, '--lang ' .. table.concat(opts.lang, ' '))
  end
  if opts.type and #opts.type > 0 then
    table.insert(parts, '--type ' .. table.concat(opts.type, ' '))
  end
  if opts.under and opts.under ~= '' then
    table.insert(parts, '--under ' .. vim.fn.shellescape(opts.under))
  end
  -- ish reports progress on stderr; the picker wants only results.
  return table.concat(parts, ' ') .. ' 2>/dev/null'
end

-- Move the rank to the head of the line.
--
-- ish prints `path:line:col:[0.71] kind symbol`, which puts the number
-- the results are ordered by in the middle of the text. Read it out and
-- print it first, so the column the eye follows is the leftmost one.
--
-- Return a line that already carries a rank unchanged. fzf-lua applies
-- `fn_transform` to the output of a command but not to a table handed
-- back from the callback, so the server path renders its own lines and
-- both paths must survive passing through here.
local function render(line)
  local fzf = require('fzf-lua')
  -- A rendered line opens with a colored rank, so read past the color
  -- before deciding whether one is already there.
  if fzf.utils.strip_ansi_coloring(line):match(RANK) then
    return line
  end
  local head, rank, tail = line:match('^(.-:%d+:%d+:)%[([%d%.]+)%]%s*(.*)$')
  if not head then
    return fzf.make_entry.file(line, { file_icons = true, colors = true })
  end
  local entry = fzf.make_entry.file(head .. tail, { file_icons = true, colors = true })
  return fzf.utils.ansi_codes.yellow(rank) .. ' ' .. entry
end

-- Take the rank back off before fzf-lua reads the path out of a line.
-- The previewer and every action parse the entry through this hook, so
-- one place covers them all.
local function unrender(entry)
  local plain = require('fzf-lua.utils').strip_ansi_coloring(entry)
  return (plain:gsub(RANK, ''))
end

-- How often to ask what the index is doing, while it is doing it.
local WATCH_MS = 700
-- Give up watching after this long. fzf-lua offers no hook for the
-- picker closing, so the watch has to end on its own or it would poll
-- for the life of the session.
local WATCH_LIMIT_MS = 10 * 60 * 1000

-- Where a statusline can read what the index is doing, empty when idle.
vim.g.ish_index_status = ''

-- Follow a refresh until it finishes, publishing what it is doing.
--
-- The index is brought up to date when the picker opens, not on every
-- keystroke, and the results improve while it runs. Say so: a picker
-- that quietly answers from yesterday's index is worse than a slow one.
local function watch(server, path)
  local timer = vim.uv.new_timer()
  local waited, told = 0, false

  local function finish(message)
    vim.g.ish_index_status = ''
    if not timer:is_closing() then
      timer:stop()
      timer:close()
    end
    if message then
      vim.notify(message, vim.log.levels.INFO)
    end
  end

  timer:start(0, WATCH_MS, vim.schedule_wrap(function()
    waited = waited + WATCH_MS
    if waited > WATCH_LIMIT_MS then
      return finish(nil)
    end
    server.status(path, function(state)
      if state.refreshing then
        told = true
        vim.g.ish_index_status = 'ish: ' .. state.refreshing
      elseif state.chunks == nil then
        finish(nil)                      -- the server stopped answering
      else
        finish(told and 'ish: index up to date' or nil)
      end
    end)
  end))
  return timer
end

-- Bind Tab to the completer, which reads the query and returns it
-- finished. Ask twice: once for the query, once for the choices to show
-- beside it when the word is still ambiguous.
local function complete_bind(cwd)
  local where = vim.fn.shellescape(cwd)
  return ('transform-query(ish-complete {q} %s)+transform-header(ish-complete --candidates {q} %s)')
    :format(where, where)
end

--- Search the whole project by meaning.
---
--- Narrow it by writing a filter into the query: `lang:cpp`, `type:doc`,
--- `type:test`, or `under:/src/`. The server reads them out of the query
--- and the embedder never sees them.
---
--- Ask the resident server, which is started on first use, and fall back
--- to running ish directly if it cannot be reached. A fresh process per
--- keystroke costs about half a second; the server answers in a tenth of
--- that.
function M.search(opts)
  opts = opts or {}
  opts.cwd = opts.cwd or root()
  local server = require('utils.ish_server')

  -- Look for changes now, once, rather than on every keystroke. The
  -- search runs against whatever is already stored and improves as the
  -- refresh lands.
  if server.ensure() then
    server.refresh(opts.cwd)
    watch(server, opts.cwd)
  end

  require('fzf-lua').fzf_live(function(query)
    local text = query_text(query)
    if text == '' then
      return nil
    end
    if server.ensure() then
      -- Render here: a table handed back from this callback reaches fzf
      -- without passing through `fn_transform`.
      return vim.tbl_map(render, server.search_now(text, opts))
    end
    return command(text, opts)
  end, {
    prompt = opts.prompt or 'Semantic❯ ',
    previewer = 'builtin',
    fn_transform = render,
    _fmt = { from = unrender },
    actions = require('fzf-lua').defaults.actions.files,
    fzf_opts = { ['--delimiter'] = ':', ['--nth'] = '4..' },
    -- Tab finishes a filter word the way a shell does, and names the
    -- choices when it cannot finish one. The values are not guessable:
    -- `lang:` takes a parser name or an alias, `under:` takes a path.
    keymap = { fzf = { ['tab'] = complete_bind(opts.cwd) } },
  })
end

--- Search only the languages given, for example { 'cpp' }.
function M.search_lang(languages, opts)
  opts = vim.tbl_extend('force', opts or {}, { lang = languages })
  opts.prompt = opts.prompt or ('Semantic[' .. table.concat(languages, ',') .. ']❯ ')
  return M.search(opts)
end

--- Search only the kinds given: code, doc, test, or config.
function M.search_type(types, opts)
  opts = vim.tbl_extend('force', opts or {}, { type = types })
  opts.prompt = opts.prompt or ('Semantic[' .. table.concat(types, ',') .. ']❯ ')
  return M.search(opts)
end

--- Search below the current file's directory.
function M.search_here(opts)
  local dir = vim.fs.dirname(vim.api.nvim_buf_get_name(0))
  opts = vim.tbl_extend('force', opts or {}, { cwd = dir ~= '' and dir or nil })
  opts.prompt = opts.prompt or 'Semantic(here)❯ '
  return M.search(opts)
end

_internal.query_text = query_text
_internal.command = command
_internal.render = render
_internal.unrender = unrender
_internal.watch = watch
_internal.complete_bind = complete_bind
M._internal = _internal

return M
