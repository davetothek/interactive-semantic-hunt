# Clients

## Use from Neovim

`contrib/nvim/ish.lua` is an fzf-lua picker. Copy it to `lua/utils/ish.lua` and
bind it:

```lua
map('n', '<leader>fi', function() require('utils.ish').search() end,
    { desc = 'Semantic search (ish)' })
```

It reads `--format grep`, so the built-in previewer opens each result at its
line, and prints the rank in the leftmost column. `search_lang({'cpp'})`,
`search_type({'doc'})`, and `search_here()` narrow it, as does a `lang:`,
`type:`, or `under:` word typed into the query.

While the index refreshes, `require('utils.ish').statusline()` renders a bar
for a statusline — `ish ███░░░░░ 38%` — and an empty string when idle. It reads `vim.g.ish_index_status`, which the picker keeps up to date, and
shows `ish ✓` briefly when a refresh finishes. Nothing is reported through
`vim.notify`: with `cmdheight = 0` there is no command line to put a message
in, so nvim draws one over the last screen row — the statusline itself.

The picker never blocks the editor: results are written as they arrive, so
typing stays smooth however long a search takes.

Saving a buffer tells the server to refresh, once a search has started it,
so an edit is searchable moments after the save. The server would otherwise
notice on its next poll, up to `refresh_seconds` later.

`contrib/nvim/ish_server.lua` keeps one `ish-mcp` process per session. It
starts on the first search and is reused after that, which cuts a keystroke
from about 500 ms to about 150 ms. Copy it beside the picker.

`contrib/nvim/ish_link.lua` opens a source of an answer at the lines it
names. Copy it to `lua/utils/ish_link.lua` and bind it:

```lua
map('n', '<leader>gf', function() require('utils.ish_link').open() end,
    { desc = 'Open the ish link under the cursor' })
```

It takes the link under the cursor, or the first link on the line. On a
marker such as `[^1]` in the prose it takes the source that the marker
names, and on a line of details below a source it takes that source. A file
URI opens the file. An http or
https link, which a server gives when it sets `link_base`, is read with curl
into a buffer named after the link, at the first line of its `#L` range, and
`:w` keeps a copy. curl reads `~/.netrc`, so a tree behind basic
authentication needs one line there. The fetch runs in the background, and
only a failure shows a message.

A tree behind the bearer token of the server gets `ISH_MCP_TOKEN` in an
Authorization header. The module sends the token only to the origin of
`ISH_REMOTE`, so a link to another host never sees it. Name more origins in
`token_origins`:

```lua
require('utils.ish_link').token_origins = { 'https://docs.example.com' }
```

curl reads the header from stdin, so the token is not on its command line.

A plugin that maps the same key when it loads takes the key from the
opener. `link()` returns the link the cursor points at, or nil, so one
mapping can serve both:

```lua
map('n', '<leader>gf', function()
  local links = require('utils.ish_link')
  local link = links.link()
  if link then
    links.open(link)
  else
    vim.cmd('DiffviewFileHistory %')
  end
end, { desc = 'Open the ish link, or the file history' })
```

`contrib/nvim/ish_ask.lua` asks a question and opens the answer in a split.
Copy it to `lua/utils/ish_ask.lua`, beside the link opener, and bind it:

```lua
map('n', '<leader>fa', function() require('utils.ish_ask').ask() end,
    { desc = 'Ask ish a question' })
```

It asks for the question and opens the split at once, with the seconds it
has waited. When `ish --ask` returns, the split holds the answer as
Markdown. The command line answers, so the `remote` server, the language
model, and the filter words in the question are the ones a shell uses. It
needs `ish` on PATH. A call can give the question, the tree, and more flags:

```lua
require('utils.ish_ask').ask('how is the watchdog configured',
    { cwd = 'docs/', args = { '--brief' } })
```

In the answer, `gf` and `<leader>gf` open the source the cursor points at in
the window the question came from, so the answer stays in view. The module
sets the keys on the buffer of the answer, where no global mapping of the
same key reaches. `split` and `link_keys` change the window and the keys:

```lua
local ask = require('utils.ish_ask')
ask.split = 'botright 15split'
ask.link_keys = { 'gf' }
```

## Use from VS Code

`contrib/vscode/` is an extension that drives the same `ish-mcp` server
from a QuickPick. Press `Ctrl+Alt+I` and type. The highlighted chunk shows
in the editor beside the picker, Enter opens it with its lines selected, and
Tab finishes a filter word. While the index refreshes, the status bar shows
`ish ███░░░░░ 38%`. `ish: Ask a question` runs `ish --ask` and opens the
answer as a Markdown document beside the editor. It needs `ish-mcp` on PATH,
and `ish` for a question, like the Neovim client, and
[its README](https://github.com/davetothek/interactive-semantic-hunt/blob/main/contrib/vscode/README.md) says how to build, install, and
release it. It ships on its own
schedule, apart from the PyPI package.

## Use from Python

```python
from ish.interfaces.python.api import Ish

with Ish("src/") as ish:
    for chunk, score in ish.search("type:doc how to configure", limit=5):
        print(score, chunk.path, chunk.symbol)
    print(ish.status())
```

`Ish` holds the index open, so a second query costs a search rather than a
process start. It offers `search()`, `chunks()`, `index()`, `refresh_all()`,
and `status()`, and reads `lang:`, `type:`, and `under:` out of the query
exactly as the other interfaces do.

## Use from an agent

`ish-mcp` serves the same search over the Model Context Protocol, so an agent
can query the index directly. Add it to a project with `.mcp.json`:

```json
{
  "mcpServers": {
    "ish": { "command": "uv", "args": ["run", "ish-mcp"] }
  }
}
```

It offers `search_code`, `ask`, `list_chunks`, `index_status`,
`refresh_index`, and `complete_filter`. `ask` answers a question from the
chunks a search finds and returns JSON: the answer with `[n]` markers, and
for each citation the path, the location, and a link. It needs a language
model, which [Answer questions](configuration.md#answer-questions) sets up. The server stays resident, so a query costs about 58 ms
rather than a process start, and a filter word completes from the registries
the server already holds rather than through a fresh `ish-complete`.

The server re-checks a tree every `refresh_seconds`, 30 by default. An
editor knows the moment a file is saved, so the Neovim and VS Code clients
call `refresh_index` then, and an edit is searchable moments later. A client
of your own should do the same.

A call may narrow one search with `lang`, `under`, `type`, and `limit`, or
write the same filters into the query text. It cannot change
what is indexed — those settings come from the config file only, so no single
call can shrink an index that another call depends on.

## Serve over HTTP

`ish-mcp` serves stdio to the client that starts it. For clients on other
machines, serve the same tools over MCP Streamable HTTP instead:

```sh
export ISH_MCP_TOKEN="$(openssl rand -hex 32)"
ish-mcp --http 0.0.0.0:8765
```

Every request goes to `/mcp` and carries the token as
`Authorization: Bearer <token>`. A request without it, or with a wrong one,
gets 401 and runs nothing. The server refuses to start without a token. A
bare port, such as `--http 8765`, listens on `127.0.0.1` only. Each request
runs on its own thread, so a slow answer for one client does not hold up
another. Run it in the tree it serves, beside an `ish-serve` that keeps the
index current.

Point a client at it:

```json
{
  "mcpServers": {
    "docs": {
      "type": "http",
      "url": "https://docs.example.com:8765/mcp",
      "headers": { "Authorization": "Bearer ${ISH_MCP_TOKEN}" }
    }
  }
}
```

The server speaks plain HTTP. Put it behind a reverse proxy that holds the
certificate when the token crosses a network you do not trust.

The `ish` command asks such a server with `--remote`. See
[Ask a server](usage.md#ask-a-server).
