# ish — Interactive Semantic Hunt

Semantic search for code, inspired by `fzf`. Point `ish` at a directory. It parses
each source file into named chunks, embeds them with a local model, and ranks them
against your query. Everything runs on your machine.

Languages: Python, C and C++, Markdown, AsciiDoc, YAML, and JSON. Documentation
and configuration are indexed beside the code they describe, so one query
searches all of it.

## Install

```sh
pip install interactive-semantic-hunt
```

Nothing in that install compiles: the default backend reaches Ollama over
HTTP with the standard library. A backend that runs the model in this
process is an extra — `[llama]` for llama.cpp, `[st]` for
sentence-transformers.

The default embedding backend is Ollama, which keeps the model resident so no
run pays a model load. Start it once and pull the embedding model:

```sh
ollama serve
ollama pull nomic-embed-text
```

Set `OLLAMA_HOST` to reach a daemon elsewhere.

Two other backends need no daemon:

- `--embedder llama.cpp` downloads a GGUF model and loads it per run. Slower per
  query, faster for a first index of a large tree. Needs the `[llama]` extra.
- `--embedder st` uses sentence-transformers. Needs the `[st]` extra.

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

Run the interactive picker and open the selection in your editor:

```sh
nvim $(ish -i src/)
```

Type to search, `up`/`down` to move, `enter` to choose, `escape` to quit.

The first search of a tree builds its index. Later searches parse only the
files that changed and embed only text they have not seen.

### Narrow a search

Write a filter into the query. It works the same in every interface:

```text
lang:cpp state machine transitions     only C and C++
under:/10.System/ state                only paths that match
type:doc how do I configure this       only prose
type:test,doc retry backoff            the tests and what they document
```

`type:` takes `code`, `doc`, `test`, and `config`.

Press Tab to finish a filter word: `ty` becomes `type:`, `lang:cp` becomes
`lang:cpp`.

## Use it from your editor or your code

| from | how | read |
|---|---|---|
| Neovim | `contrib/nvim/ish.lua`, an fzf-lua picker | [clients](https://davetothek.github.io/interactive-semantic-hunt/clients/#use-from-neovim) |
| VS Code | `contrib/vscode/`, a QuickPick on `Ctrl+Alt+I` | [clients](https://davetothek.github.io/interactive-semantic-hunt/clients/#use-from-vs-code) |
| Python | `from ish.interfaces.python.api import Ish` | [clients](https://davetothek.github.io/interactive-semantic-hunt/clients/#use-from-python) |
| an agent | `ish-mcp`, a Model Context Protocol server | [clients](https://davetothek.github.io/interactive-semantic-hunt/clients/#use-from-an-agent) |

To keep the index of a large tree current in the background, run
`ish-serve /path/to/tree`. Every client then reads the index and refreshes
nothing itself. See
[indexing](https://davetothek.github.io/interactive-semantic-hunt/indexing/#keep-the-index-current).

## Configure

Every command-line option is also a key in `.ish/config.toml` at the root of
your repository:

```toml
limit = 10
ignore = [".git", ".venv", "build", "node_modules"]

# Regular expressions, searched against the path.
exclude = ["/vendor/", "/generated/", "_pb2\\.py$"]
```

Keep generated code out of the index. It is large, nobody searches it by
meaning, and embedding costs about one chunk per second.

## Documentation

- [Use](https://davetothek.github.io/interactive-semantic-hunt/usage/):
  every filter, every option, and the picker themes.
- [Clients](https://davetothek.github.io/interactive-semantic-hunt/clients/):
  Neovim, VS Code, Python, and MCP.
- [Configure](https://davetothek.github.io/interactive-semantic-hunt/configuration/):
  config files and their order, keeping generated code out, the model's
  context window.
- [Index](https://davetothek.github.io/interactive-semantic-hunt/indexing/):
  where the index lives, federation, worktrees, and `ish-serve`.
- [Extend](https://davetothek.github.io/interactive-semantic-hunt/extending/):
  add a language or an embedding backend.

## Develop

```sh
uv sync
uv run poe check   # lint, typecheck, test
uv run --group docs mkdocs serve   # preview the documentation site
```

The architecture is ports and adapters. `.claude/CLAUDE.md` describes the
layers, the composition root, and the decisions behind them.
