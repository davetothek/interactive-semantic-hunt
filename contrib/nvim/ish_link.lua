-- Open the link under the cursor in a buffer, at the lines it names.
--
-- An answer of `ish --ask` lists each source as a link with its lines in
-- the fragment, such as `file:///src/app.py#L12-27`, or with `link_base`
-- set on the server, `https://docs.example.com/tree/app.py#L12-27`. The
-- prose cites a source with a marker such as `[^1]`, and the opener
-- follows a marker to its source.
--
-- A file URI opens the file. An http or https link is read with curl into
-- a buffer named after the link, so the filetype and the jump work as
-- for a file on disk, and `:w` keeps a copy. curl reads ~/.netrc, so a
-- tree behind basic authentication needs one line there. Any other
-- scheme goes to the system opener.
--
-- A tree behind the bearer token of the server gets `ISH_MCP_TOKEN` in an
-- Authorization header. The token goes only to the origin of `ISH_REMOTE`
-- and to the origins in `token_origins`, so a link to any other host
-- never sees it. curl reads the header from stdin, so the token is not
-- on its command line.
--
-- The fetch runs in the background. Neovim keeps drawing while it waits,
-- and the buffer opens when the text arrives. Only a failure is reported
-- through vim.notify: with `cmdheight = 0` a message draws over the
-- statusline, so a fetch that works says nothing.
--
-- `follow()` is the opener for `gf`. It leaves the name of a file, and a
-- line with no link, to the built-in command.
--
--   vim.keymap.set('n', 'gf', function() require('utils.ish_link').follow() end,
--     { desc = 'Open the ish link, or the file under the cursor' })

local M = {}

-- Origins besides the one of `ISH_REMOTE` that get the token, such as
-- { "https://docs.example.com" }.
M.token_origins = {}

local URL = "%a[%w+.-]*://[^%s%)%]>\"'`]+"

-- Return the marker of a source, such as `1` for `[^1]`, when the cursor
-- is on one in *line*.
local function marker_at(line, column)
  local from = 1
  while true do
    local start, stop, label = line:find("%[%^([^%]]+)%]", from)
    if not start then
      return nil
    end
    if column >= start and column <= stop then
      return label
    end
    from = stop + 1
  end
end

-- Return the link of the source that *label* marks in the current buffer.
local function source_of(label)
  local head = "[^" .. label .. "]:"
  for _, line in ipairs(vim.api.nvim_buf_get_lines(0, 0, -1, false)) do
    if vim.startswith(line, head) then
      return line:match(URL)
    end
  end
  return nil
end

-- Return the link of the source that the indented line at *row* describes.
-- An answer puts the kind and the directory of a source on indented lines
-- below its link, so the link is on the first line above that starts at
-- the margin.
local function source_above(row)
  for above = row - 1, 1, -1 do
    local line = vim.api.nvim_buf_get_lines(0, above - 1, above, false)[1]
    if not line:match("^%s+%S") then
      return line:match(URL)
    end
  end
  return nil
end

-- Return the link the cursor points at, or nil. In order: the link under
-- the cursor, the source of the marker under the cursor, the first link
-- on the line, and the source that an indented line describes. The cursor
-- often sits on the label of a Markdown link or on a marker in the prose,
-- not on the target.
local function link_at_cursor()
  local line = vim.api.nvim_get_current_line()
  local row, column = unpack(vim.api.nvim_win_get_cursor(0))
  column = column + 1
  local first
  local from = 1
  while true do
    local start, stop = line:find(URL, from)
    if not start then
      break
    end
    local found = line:sub(start, stop)
    if column >= start and column <= stop then
      return found
    end
    first = first or found
    from = stop + 1
  end
  local label = marker_at(line, column)
  local marked = label and source_of(label)
  if marked or first then
    return marked or first
  end
  return line:match("^%s+%S") and source_above(row) or nil
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

local function origin(link)
  return link:lower():match("^(https?://[^/?#]+)")
end

-- Return the token when *base* is on an origin that may receive it.
local function token_for(base)
  local token = vim.env.ISH_MCP_TOKEN
  if not token or token == "" then
    return nil
  end
  local wanted = origin(base)
  local allowed = vim.list_extend({ vim.env.ISH_REMOTE or "" }, M.token_origins)
  for _, candidate in ipairs(allowed) do
    if wanted and origin(candidate) == wanted then
      return token
    end
  end
  return nil
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

--- Return the link the cursor points at, or nil when it points at none.
--- A mapping that shares its key uses this to decide what the key does.
function M.link()
  return link_at_cursor()
end

-- Return true when the cursor is on the name of a file that the built-in
-- `gf` finds. `findfile` returns a link as it is, so a link is not a file
-- here.
local function on_file()
  local name = vim.fn.expand("<cfile>")
  return not name:find("://", 1, true) and vim.fn.findfile(name) ~= ""
end

--- Open the link the cursor points at, as `gf`. The name of a file under
--- the cursor, and a line with no link, go to the built-in `gf`.
function M.follow()
  local link = not on_file() and link_at_cursor()
  if link then
    M.open(link)
  else
    vim.api.nvim_feedkeys("gf", "n", false)
  end
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
  local command = { "curl", "--fail", "--silent", "--show-error", "--location", "--netrc-optional" }
  local token = token_for(base)
  local stdin
  if token then
    vim.list_extend(command, { "--header", "@-" })
    stdin = "Authorization: Bearer " .. token .. "\n"
  end
  table.insert(command, base)
  vim.system(
    command,
    { text = true, stdin = stdin },
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

M._internal = {
  link_at_cursor = link_at_cursor,
  split = split,
  decode = decode,
  token_for = token_for,
}

return M
