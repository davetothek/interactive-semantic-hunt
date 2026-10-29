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

## Use from VS Code

`contrib/vscode/` is an extension that drives the same `ish-mcp` server
from a QuickPick. Press `Ctrl+Alt+I` and type. The highlighted chunk shows
in the editor beside the picker, Enter opens it with its lines selected, and
Tab finishes a filter word. While the index refreshes, the status bar shows
`ish ███░░░░░ 38%`. It needs `ish-mcp` on PATH, like the Neovim client, and
[its README](../contrib/vscode/README.md) says how to build, install, and
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

It offers `search_code`, `list_chunks`, `index_status`, `refresh_index`, and
`complete_filter`. The server stays resident, so a query costs about 58 ms
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
