-- lua/utils/ish.lua
-- Semantic code search backed by ish, presented through fzf-lua.
--
-- Each keystroke runs one ish query. The index is already built, so a
-- query costs a search rather than a scan, and fzf-lua debounces the
-- typing.

local M = {}

local function root()
  local found = vim.fs.find('.git', { upward = true, path = vim.uv.cwd() })[1]
  return found and vim.fs.dirname(found) or vim.uv.cwd()
end

local function command(query, opts)
  local parts = {
    'ish',
    vim.fn.shellescape(query),
    vim.fn.shellescape(opts.cwd or root()),
    '--format grep',
    '--limit ' .. (opts.limit or 40),
  }
  if opts.lang and #opts.lang > 0 then
    table.insert(parts, '--lang ' .. table.concat(opts.lang, ' '))
  end
  if opts.under then
    table.insert(parts, '--under ' .. vim.fn.shellescape(opts.under))
  end
  -- ish reports progress on stderr; the picker wants only results.
  return table.concat(parts, ' ') .. ' 2>/dev/null'
end

--- Search the whole project by meaning.
--- Type `lang:cpp` or `under:/src/` inside the query to narrow it.
function M.search(opts)
  opts = opts or {}
  require('fzf-lua').fzf_live(function(query)
    if not query or query == '' then
      return nil
    end
    return command(query, opts)
  end, {
    prompt = opts.prompt or 'Semantic❯ ',
    previewer = 'builtin',
    fn_transform = function(line)
      return require('fzf-lua').make_entry.file(line, { file_icons = true, colors = true })
    end,
    actions = require('fzf-lua').defaults.actions.files,
    fzf_opts = { ['--delimiter'] = ':', ['--nth'] = '4..' },
  })
end

--- Search only the languages given, for example { 'cpp' }.
function M.search_lang(languages, opts)
  opts = vim.tbl_extend('force', opts or {}, { lang = languages })
  opts.prompt = opts.prompt or ('Semantic[' .. table.concat(languages, ',') .. ']❯ ')
  return M.search(opts)
end

--- Search below the current file's directory.
function M.search_here(opts)
  local dir = vim.fs.dirname(vim.api.nvim_buf_get_name(0))
  opts = vim.tbl_extend('force', opts or {}, { cwd = dir ~= '' and dir or nil })
  opts.prompt = opts.prompt or 'Semantic(here)❯ '
  return M.search(opts)
end

return M
