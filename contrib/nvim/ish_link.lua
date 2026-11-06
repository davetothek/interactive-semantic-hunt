-- Open the link under the cursor in a buffer, at the lines it names.
--
-- An answer of `ish --ask` lists each source as a link with its lines in
-- the fragment, such as `file:///src/app.py#L12-27`, or with `link_base`
-- set on the server, `https://docs.example.com/tree/app.py#L12-27`. A
-- file URI opens the file. An http or https link is read with curl into
-- a buffer named after the link, so the filetype and the jump work as
-- for a file on disk, and `:w` keeps a copy. curl reads ~/.netrc, so a
-- tree behind basic authentication needs one line there. Any other
-- scheme goes to the system opener.
--
-- The fetch runs in the background. Neovim keeps drawing while it waits,
-- and the buffer opens when the text arrives. Only a failure is reported
-- through vim.notify: with `cmdheight = 0` a message draws over the
-- statusline, so a fetch that works says nothing.
--
--   vim.keymap.set('n', '<leader>gf', function() require('utils.ish_link').open() end,
--     { desc = 'Open the ish link under the cursor' })

local M = {}

local URL = "%a[%w+.-]*://[^%s%)%]>\"'`]+"

-- Return the link under the cursor, or the first link on the line. The
-- cursor often sits on the label of a Markdown link, not on its target.
local function link_at_cursor()
  local line = vim.api.nvim_get_current_line()
  local column = vim.api.nvim_win_get_cursor(0)[2] + 1
  local first
  local from = 1
  while true do
    local start, stop = line:find(URL, from)
    if not start then
      return first
    end
    local found = line:sub(start, stop)
    if column >= start and column <= stop then
      return found
    end
    first = first or found
    from = stop + 1
  end
end

local function decode(text)
  return (text:gsub("%%(%x%x)", function(hex)
    return string.char(tonumber(hex, 16))
  end))
end

-- Return the link without its fragment, and the first line the fragment
-- names, or nil when it names no line.
local function split(link)
  local base, fragment = link:match("^([^#]*)#?(.*)$")
  return base, tonumber(fragment:match("^L(%d+)"))
end

local function jump(line)
  if line then
    pcall(vim.api.nvim_win_set_cursor, 0, { line, 0 })
    vim.cmd("normal! zz")
  end
end

local function show(base, line, text)
  local buffer = vim.fn.bufnr(base)
  if buffer == -1 then
    buffer = vim.api.nvim_create_buf(true, false)
    vim.api.nvim_buf_set_name(buffer, base)
  end
  local lines = vim.split(text, "\n", { plain = true })
  if lines[#lines] == "" then
    table.remove(lines)
  end
  vim.api.nvim_buf_set_lines(buffer, 0, -1, false, lines)
  vim.bo[buffer].buftype = "nofile"
  vim.bo[buffer].modified = false
  local name = decode(base:match("([^/]+)$") or "")
  local filetype = vim.filetype.match({ filename = name, buf = buffer })
  if filetype then
    vim.bo[buffer].filetype = filetype
  end
  vim.api.nvim_set_current_buf(buffer)
  jump(line)
end

-- Open *link*, or the link under the cursor when none is given.
function M.open(link)
  link = link or link_at_cursor()
  if not link then
    vim.notify("ish: no link under the cursor", vim.log.levels.WARN)
    return
  end
  local base, line = split(link)
  if base:match("^file://") then
    vim.cmd.edit(vim.fn.fnameescape(decode(base:gsub("^file://", ""))))
    jump(line)
    return
  end
  if not base:match("^https?://") then
    vim.ui.open(link)
    return
  end
  vim.system(
    { "curl", "--fail", "--silent", "--show-error", "--location", "--netrc-optional", base },
    { text = true },
    function(result)
      vim.schedule(function()
        if result.code ~= 0 then
          local reason = vim.trim(result.stderr or "")
          vim.notify("ish: cannot fetch " .. base .. ": " .. reason, vim.log.levels.ERROR)
          return
        end
        show(base, line, result.stdout or "")
      end)
    end
  )
end

M._internal = { link_at_cursor = link_at_cursor, split = split, decode = decode }

return M
