# ish — Interactive Semantic Hunt

Semantic search for code, inspired by `fzf`. Point `ish` at a directory. It parses
each source file into named chunks, embeds them with a local model, and ranks them
against your query. Everything runs on your machine.

Languages: Python, C and C++, Markdown, and AsciiDoc. Documentation is indexed
beside the code it describes, so one query searches both.

## Install

```sh
uv sync
```

The default embedding backend is Ollama, which keeps the model resident so no
run pays a model load. Start it once and pull the embedding model:

```sh
ollama serve
ollama pull nomic-embed-text
```

Set `OLLAMA_HOST` to reach a daemon elsewhere.

Two other backends need no daemon:

- `--embedder llama.cpp` downloads a GGUF model and loads it per run. Slower per
  query, faster for a first index of a large tree.
- `--embedder st` uses sentence-transformers. Install it with `uv sync --extra st`.

## Use

List every chunk under a path:

```sh
ish src/
```

```
src/ish/domain/chunk.py:7-28  class  Chunk
```

Search for a query:

```sh
ish "parse a python file" src/
```

```
[0.71] src/ish/adapters/parser/python.py:14-31  method  PythonParser.parse
```

Run the interactive picker and open the selection in your editor. Type to
search, `up`/`down` or `ctrl+p`/`ctrl+n` to move, `enter` to choose, `escape`
to quit. Narrow without leaving the query line:

```text
state machine transitions              every language
lang:cpp state machine transitions     the implementation
lang:yaml under:/10.System/ state      the tests that cover it
type:doc how do I configure this       the prose, not the code
type:test,doc retry backoff            the tests and what they document
```

`lang:`, `under:`, and `type:` work in the query line of every interface —
the command line, the picker, Neovim, and MCP. The words are taken out
before the query is embedded, so the model sees the question rather than
how it was narrowed.

`type:` sorts every chunk into exactly one kind. A path decides before a
language does, so a YAML fixture counts as a test rather than as config:

| kind | what it holds |
|---|---|
| `code` | Python, C, C++, and anything a plugin parser adds |
| `doc` | Markdown and AsciiDoc |
| `test` | anything under `tests/`, `spec/`, `fixtures/`, plus `test_*` and `conftest.py` |
| `config` | YAML, JSON, and TOML outside a test path |

```sh
nvim $(ish -i src/)
```

### Options

| Flag | Purpose |
|---|---|
| `-i`, `--interactive` | Run the TUI picker |
| `--embedder {llama.cpp,ollama,st}` | Select the embedding backend (default: ollama) |
| `-v`, `-vv` | Increase log detail |
| `--color {auto,always,never}` | Control log color |
| `--limit N` | Maximum search results |
| `--ignore DIR ...` | Directory names to skip |
| `--include REGEX ...` | Index only paths matching these patterns |
| `--exclude REGEX ...` | Never index paths matching these patterns |
| `--git`, `--no-git` | Skip files git ignores (default: on) |
| `--lang LANG ...` | Show results only from these languages |
| `--under REGEX` | Show results only from matching paths |
| `--type TYPE ...` | Show results only of these kinds: `code`, `doc`, `test`, `config` |
| `--model NAME` | Override the backend model |
| `--reindex` | Discard the stored index and build it again |
| `--no-cache` | Index in memory only, leaving nothing on disk |

Logs go to stderr, so you can pipe stdout safely.

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

`contrib/nvim/ish_server.lua` keeps one `ish-mcp` process per session. It
starts on the first search and is reused after that, which cuts a keystroke
from about 500 ms to about 150 ms. Copy it beside the picker.

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

It offers `search_code`, `list_chunks`, and `index_status`. The server stays
resident, so a query costs about 58 ms rather than a process start.

A call may narrow one search with `lang`, `under`, `type`, and `limit`, or
write the same filters into the query text. It cannot change
what is indexed — those settings come from `ish.toml` only, so no single call can
shrink an index that another call depends on.

## Index

The index persists in SQLite under `$XDG_DATA_HOME/ish/`, one file per scanned
tree. A repeated query reuses it, so only changed files are parsed and only new
text is embedded. A renamed file re-embeds nothing.

Each index records the tree it was built from, so searching a directory also
searches every index below it. Index the parts of a large project separately and
search the whole from its root:

```sh
ish "warm" project/docs        # index one part
ish "warm" project/firmware    # and another
ish "how is exposure set" project    # searches both
```

Searching a parent never rewrites an index below it. Pass `--no-federate` to use
only the index of the exact path.

The index records where each chunk is — its path, line range, kind, and name —
together with the embedding vector. It does not store the source, so it is not a
second readable copy of your code. Previews are read from the file, which also
means they always show the current content.

## Configure

Every command-line option is also a key in `ish.toml`, under the same name.
Put project settings in `ish.toml` at the root of your repository:

```toml
embedder = "ollama"
model = "mxbai-embed-large"
limit = 10
ignore = [".git", ".venv", "build", "node_modules"]

# Regular expressions, searched against the path.
exclude = ["/vendor/", "_pb2\\.py$", "(_test|_spec)\\.py$"]
```

`include` and `exclude` take regular expressions rather than globs, so `/vendor/`
matches at any depth and alternation works. `exclude` wins over `include`.

`--git` is on by default, so anything a `.gitignore` covers stays out of the
index. Pass `--no-git` to index it anyway.

`--lang` and `--under` narrow what a search *returns*. They never change what is
indexed, so a narrowed query cannot shrink the index:

```sh
ish "how is ranking done" --lang python
ish "installation steps" --lang markdown asciidoc
ish "parse a header" --under '/include/'
```

User-level defaults go in `~/.config/ish/ish.toml`. Later sources win:

```
defaults < ~/.config/ish/ish.toml < ./ish.toml < ISH_* environment < command line
```

Set any option from the environment with the `ISH_` prefix, for example
`ISH_LIMIT=20` or `ISH_IGNORE=build,dist`.

## Develop

```sh
uv run poe check   # lint, typecheck, test
```

The architecture is ports and adapters. `spec.md` holds the requirements, and
`.claude/CLAUDE.md` describes the layers and the composition root.
