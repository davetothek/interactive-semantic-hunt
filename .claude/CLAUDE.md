# CLAUDE.md — ish project guide

## What this project is

`ish` (Interactive Semantic Hunt) is an interactive semantic search tool for code, inspired by `fzf`. The full spec lives in `spec.md` at the project root — read it before doing substantial work.

## Current state

MVP 1 is complete, and the semantic-search slice from spec §15 is implemented. The project has a working scan pipeline, semantic query, and an interactive TUI.

### What exists

- `src/ish/domain/chunk.py` — `Chunk` dataclass (frozen, slots). The sole domain model.
- `src/ish/application/ports/` — `Parser`, `Embedder`, and `VectorStore` protocols.
- `src/ish/application/scan.py` — scan use case (discover, read, parse).
- `src/ish/application/search.py` — search use case (scan, embed, store, query).
- `src/ish/adapters/parser/python.py` — AST-based Python parser.
- `src/ish/adapters/embedder/` — llama.cpp, Ollama, and sentence-transformers backends, plus a disk-cache wrapper (`cached.py`).
- `src/ish/adapters/vector_store/pure_python.py` — in-memory cosine-similarity store.
- `src/ish/interfaces/cli/` — argument parsing (`config.py`), logging (`log.py`), and the entry point (`main.py`).
- `src/ish/interfaces/tui/app.py` — Textual TUI, run with `ish -i`.
- Unit and integration tests under `tests/`.

### What does not exist yet

- `src/ish/interfaces/python/api.py` — skeleton `Ish` class only.
- MCP, persistent or incremental indexing, `.ishignore`, Git ignore integration, Tree-sitter, non-Python languages, configuration files, HTTP API.

## Architecture

Hexagonal / ports-and-adapters. The dependency direction is strict:

```
interfaces → application → domain
                 ↓
               ports
                 ↑
              adapters
```

### Dependency rules — never violate these

- `domain` must not import `application`, `adapters`, or `interfaces`.
- `application` may import `domain` and `application.ports`. It must not import concrete adapters or interfaces.
- `interfaces` may import `application` and `domain`.
- `adapters` implement ports. They may import `domain` and the port they implement.
- Python `ast` usage belongs only in the Python parser adapter.
- Terminal / argument handling belongs only in the CLI interface.

### Key locations

| Layer | Path | Purpose |
|---|---|---|
| Domain | `src/ish/domain/chunk.py` | `Chunk` dataclass |
| Composition | `src/ish/bootstrap.py` | Composition root — all wiring and registries |
| Composition | `src/ish/settings.py` | Option set — one source of truth for CLI flags and TOML keys |
| Port | `src/ish/application/ports/parser.py` | `Parser` Protocol and `ParseError` |
| Port | `src/ish/application/ports/embedder.py` | `Embedder` Protocol |
| Port | `src/ish/application/ports/vector_store.py` | `VectorStore` Protocol |
| Application | `src/ish/application/scan.py` | Scan use case (orchestration) |
| Application | `src/ish/application/index.py` | Index use case (staleness, orphans, embedding) |
| Application | `src/ish/application/search.py` | Search use case (refresh, then query) |
| Adapter | `src/ish/adapters/parser/python.py` | Python AST parser |
| Adapter | `src/ish/adapters/parser/markup.py` | Markdown and AsciiDoc sections |
| Adapter | `src/ish/adapters/parser/tree_sitter.py` | Tree-sitter parser, C and C++ flavor |
| Adapter | `src/ish/adapters/embedder/` | Embedding backends and disk cache |
| Adapter | `src/ish/adapters/vector_store/sqlite.py` | Persistent vector store (default) |
| Adapter | `src/ish/adapters/vector_store/pure_python.py` | In-memory vector store (`--no-cache`, tests) |
| Interface | `src/ish/interfaces/format.py` | Shared CLI/TUI output formatting |
| Interface | `src/ish/interfaces/cli/args.py` | Argument parsing, derived from `Settings` |
| Interface | `src/ish/interfaces/cli/main.py` | CLI entry point |
| Interface | `src/ish/interfaces/tui/app.py` | Textual TUI (`ish -i`) |
| Interface | `src/ish/interfaces/mcp/protocol.py` | MCP stdio JSON-RPC transport |
| Interface | `src/ish/interfaces/mcp/server.py` | MCP tools (`ish-mcp`) |
| Interface | `src/ish/interfaces/python/api.py` | Python API (future) |


## Tooling

- **Python ≥ 3.14** — required. Use modern import paths:
  - `collections.abc` for `Sequence`, `Mapping`, `Iterable`, etc. Do not import these from `typing`.
  - `typing` only for `Protocol`, `runtime_checkable`, `TypeAlias`, `TypeVar`, and similar typing-only constructs.
  - Do not use `from __future__ import annotations`. Python 3.14 defers annotation evaluation natively (PEP 649).
- **uv** — package manager. The lock file is `uv.lock`.
- **ruff** — linter and formatter. Config in `pyproject.toml`. Line length 88, double quotes, space indent. Cache in `.cache/ruff`.
- **ty** — type checker (dev dependency).
- **pytest** — test runner. Cache in `.cache/pytest`.
- **pytest-cov** — coverage reporting. Fail threshold is 90%.
- **poethepoet** — task runner (dev dependency).

### Poe targets

| Command | What it does |
|---|---|
| `poe test` | Run pytest with coverage |
| `poe lint` | Run ruff check on `src` and `tests` |
| `poe format` | Run ruff format on `src` and `tests` |
| `poe typecheck` | Run ty on `src` |
| `poe check` | Run lint → typecheck → test (all of the above) |


## Testing conventions

- Unit tests go in `tests/unit/` mirroring the source tree (`tests/unit/application/`, `tests/unit/adapters/`).
- Integration tests go in `tests/integration/cli/`.
- Use fake/stub implementations when testing application orchestration. Do not couple application tests to the real parser.
- Test the parser adapter against known Python source strings. Cover: functions, async functions, classes, methods, async methods, multiple definitions, qualified names, line numbers, source text extraction, and syntax error handling.
- At least one integration test should run the CLI against a temp project with nested directories and verify output.

## CLI output format

```
src/foo.py:12-27  function  parse_config
src/foo.py:31-58  class     ConfigLoader
src/foo.py:35-42  method    ConfigLoader.load
```

## Composition

`src/ish/bootstrap.py` is the composition root. It is the only module that imports both application code and concrete adapters, and it owns the registries:

- `EMBEDDERS` — embedding backends by CLI name. Register new backends here; the `--embedder` choices derive from this dict.
- `PARSERS` — source parsers by language name. Register new parsers (Tree-sitter, AsciiDoc, ...) here; file discovery derives its suffix set from each parser's `suffixes`, and the `languages` option selects which are built.

Registered languages: `python`, `markdown`, `asciidoc`, `cpp`.

- **Markdown and AsciiDoc share one parser.** They differ only in the heading marker and the suffixes, so `MarkupParser` is built twice with different arguments. A section runs to the next heading, its symbol is the heading path, and fenced blocks are skipped so `# comment` in a code sample is not a heading.
- **C and C++ share one parser**, registered as `cpp`, which owns `.h`. The C++ grammar reads nearly all C, and splitting them would leave every header ambiguous. A type is only a definition when it has a body, so `struct Node *next` does not become a second chunk. An attached doc comment travels with the definition, for the same reason decorators do.
- Tree-sitter is error tolerant. A partly broken file returns whatever parsed; `ParseError` is raised only when nothing did.

Adding a language is one new module under `adapters/parser/` plus one `PARSERS` entry. It must not require a change to `Scan`, the `Parser` port, or any interface. A parser declares `language` (its identity, stamped onto every chunk it emits) and `suffixes`. Two parsers claiming one suffix is a hard error; resolve it with the `languages` option.

Interfaces call `bootstrap.build_scan(settings)` / `bootstrap.build_search(settings)` and never construct adapters themselves. No dependency injection framework.

## Configuration

`src/ish/settings.py` declares every configurable option once, as fields on the frozen `Settings` dataclass. The CLI builds its flags from those fields and the TOML loader accepts the same names, so the two interfaces cannot drift. Add an option by adding one field — never by editing `args.py`.

Precedence, resolved only in `load_settings()`:

```
defaults < ~/.config/ish/ish.toml < ./ish.toml (searched upward) < ISH_* env < CLI flags
```

- **No use case ever receives a `Settings` object.** `Scan` and `Search` take explicit constructor arguments; `bootstrap` reads the settings and passes values. Keep the dependency arrow pointing at values, not at a config bag.
- There is deliberately no way to declare a config-only or CLI-only option. `tests/unit/test_settings.py` enforces the parity in both directions.
- An unknown key warns and is skipped. A malformed or unreadable file raises `ConfigError` and exits 1.

`src/ish/__init__.py` must stay free of layer imports — `tests/unit/test_package.py` enforces this in a fresh interpreter.

## Indexing

The index persists in SQLite, one file per scanned tree, under `$XDG_CACHE_HOME/ish/`.

- **Vectors are keyed by `(content_hash, model_id)`, not by path.** A renamed file re-embeds nothing, an edited function re-embeds only itself, and switching models keeps both sets.
- **Staleness is two-tier.** Compare `(mtime_ns, size)` first; read and hash only what differs. Never hash every file on every query.
- **Orphans** are pruned to the scanned tree only, so indexing a subdirectory never discards its siblings.
- `remove_files` and `clear` keep vectors, since restoring a file should cost no embedding. `prune_vectors` sweeps unreferenced ones on demand.
- Interfaces must call `Search.close()`, which releases the database.

Measured on this repo (33 files, 104 chunks): a cold index costs ~87s with Ollama, ~51s with llama.cpp, which parallelizes bulk embedding better. A warm query costs ~0.20s.

Embedding models are often trained with a task prefix for stored text and another for queries, so the `Embedder` port has `embed_documents` and `embed_query` rather than one method. The table lives in `adapters/embedder/prefixes.py`, keyed by model name, because the convention belongs to the model and not to the backend serving it. Measured on this repo with nomic-embed-text over 16 queries: top-1 accuracy 62% without the prefixes, 75% with.

Anything that changes what a stored vector means — the prefixes, or `embed_text()` — must bump `SCHEMA_VERSION` in the SQLite adapter, which discards the old index instead of mixing it.

The default backend is Ollama, reached over HTTP with the standard library. Do not add a client package for it: importing the `ollama` package cost 176ms, which was 71% of a warm query, while the request itself takes ~38ms.

## TUI

`ish -i` is the primary interface. Test it with Textual's pilot in `tests/unit/interfaces/test_tui_app.py`, which drives a real mounted DOM headless. It is not excluded from coverage.

- The query field keeps focus at all times. `up`/`down` and `ctrl+p`/`ctrl+n` move the result highlight through app bindings, and `enter` chooses the highlighted result. Never move focus to the list to navigate it.
- `ENABLE_COMMAND_PALETTE` is off, because Textual binds `ctrl+p` to the palette and would shadow the previous-result key.
- Indexing runs on a worker thread and searching on another, so **any store the TUI touches must be safe to use off the thread that opened it**. The SQLite adapter opens with `check_same_thread=False` and guards every statement with one lock. A single-threaded test suite will not catch a regression here; `TestThreadSafety` exists for that.

Measured with a warm index: startup 0.05 s, last keystroke to results 0.18 s including the 200 ms debounce.

## MCP

`ish-mcp` speaks the Model Context Protocol on stdio. It is the third interface, wired through `bootstrap` exactly like the CLI and TUI, and it offers `search_code`, `list_chunks`, and `index_status`.

- **Nothing may write to stdout except a protocol message.** stdout is the transport; logging goes to stderr. A test asserts every emitted line parses as JSON.
- The protocol layer is hand-written on the standard library. MCP over stdio is newline-delimited JSON-RPC 2.0, which is small enough not to justify a dependency that pulls in pydantic and starlette.
- The server is long-lived, so it holds one `Search` per scanned root and keeps the index warm. That is the whole latency advantage: a query costs a search, not a process start.
- A tool failure is reported through `isError`, not a JSON-RPC error, so the host can show the model what went wrong.

Measured: ~58 ms per `search_code` call, against ~190 ms for the same query through the CLI.

## Ranking

Search fuses two rankings with weighted Reciprocal Rank Fusion: the vector order, and a BM25 order from an FTS5 table kept in step with `chunks` by triggers.

**The lexical half runs only when `is_code_like(query)` says the query names something** — an underscore, an all-capital word, or mixed case. This gate is not a nicety. Measured on this repo over 20 queries:

| ranking | conceptual | identifier | combined |
|---|---|---|---|
| vector only | 90% / MRR .925 | 90% / MRR .910 | 90% / MRR .918 |
| hybrid, always on | 80% / MRR .883 | 90% / MRR .950 | 85% / MRR .917 |
| hybrid, gated (shipped) | 90% / MRR .925 | 90% / MRR .950 | 90% / MRR .938 |

Fusing a lexical order into a plain description **costs 10 points of top-1 accuracy**, because the vector ranking is already the better signal there. Weighting alone did not recover it; a 3:1 sweep still lost 5 points. Do not remove the gate, and re-run the benchmark before changing `SEMANTIC_WEIGHT`.

The reported score stays the cosine similarity, so the number means the same thing whether or not the lexical half ran. `--no-hybrid` turns the lexical half off entirely.

## Filesystem discovery

The scan recursively finds files whose suffix a registered parser claims and ignores at minimum: `.git/`, `.venv/`, `venv/`, `__pycache__/`. Directory symlinks are not followed.

## Writing style

This project uses STE-flavored Simplified Technical English for prose. See `.claude/skills/ste-writing/SKILL.md` for the rules. Apply them to documentation, commit messages, comments, and error strings. Do not apply them to code or identifiers.

## Comment rules

These rules are mandatory. They add to the STE skill (`.claude/skills/ste-writing/SKILL.md`), which already applies strict mode to comments.

- **Imperative mood.** Write comments as commands: "Return the parsed chunk", not "Returns the parsed chunk" or "This returns the parsed chunk".
- **No history.** A comment describes the code as it is now. Do not reference what the code used to do, what changed, or why it was refactored. That information belongs in commit messages, not in source files.
- **Intent, not mechanics.** Explain *why* the code exists or *what contract* it fulfills. Do not restate what the code already says. `# increment counter` above `counter += 1` is noise.
- **No TODO without a reason.** Every `# TODO` must say *why* the work is deferred, not just *what* to do.
- **No attribution.** Do not write "added by", "author:", or any name/date in comments. Use `git blame` for that.
- **No commented-out code.** Delete dead code. Version control keeps the history.

## Ground rules

- **Read `spec.md` first** for any substantial work. It is the source of truth for requirements.
- **Do not add out-of-scope features.** Embeddings, the in-memory vector store, and the TUI are in scope (spec §15). Still excluded: MCP, persistent or incremental indexing, Tree-sitter, non-Python languages, config files, `.ishignore`, Git ignore integration, HTTP API. See spec §13.
- **Do not silently swallow errors.** Parse failures must surface — either skip the file and report to stderr, or return a structured error.
- **Keep the domain model clean.** No embedding vectors, AST nodes, parser internals, or UI state in `Chunk`.
- **Run tests before declaring done.**
- **Ask before adding new dependencies.**
- **Do not push to main directly.**
