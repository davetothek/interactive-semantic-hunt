# ish — Interactive Semantic Hunt

Semantic search for code, inspired by `fzf`. Point `ish` at a directory. It parses
each source file into named chunks, embeds them with a local model, and ranks them
against your query. Everything runs on your machine.

## Install

```sh
uv sync
```

The default embedding backend is llama.cpp. It downloads a GGUF model on first use.
Two other backends are optional:

```sh
uv sync --extra st      # sentence-transformers
uv sync --extra ollama  # Ollama daemon
```

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
| `--embedder {llama.cpp,ollama,st}` | Select the embedding backend |
| `-v`, `-vv` | Increase log detail |
| `--color {auto,always,never}` | Control log color |

Logs go to stderr, so you can pipe stdout safely.

## Develop

```sh
uv run poe check   # lint, typecheck, test
```

The architecture is ports and adapters. `spec.md` holds the requirements, and
`.claude/CLAUDE.md` describes the layers and the composition root.
