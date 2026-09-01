# ish — Interactive Semantic Hunt

Semantic search for code, inspired by `fzf`. Point `ish` at a directory. It parses
each source file into named chunks, embeds them with a local model, and ranks them
against your query. Everything runs on your machine.

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

Run the interactive picker and open the selection in your editor:

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
| `--model NAME` | Override the backend model |
| `--reindex` | Discard the stored index and build it again |
| `--no-cache` | Index in memory only, leaving nothing on disk |

Logs go to stderr, so you can pipe stdout safely.

## Index

The index persists in SQLite under `$XDG_CACHE_HOME/ish/`, one file per scanned
tree. A repeated query reuses it, so only changed files are parsed and only new
text is embedded. A renamed file re-embeds nothing.

## Configure

Every command-line option is also a key in `ish.toml`, under the same name.
Put project settings in `ish.toml` at the root of your repository:

```toml
embedder = "ollama"
model = "mxbai-embed-large"
limit = 10
ignore = [".git", ".venv", "build", "node_modules"]
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
