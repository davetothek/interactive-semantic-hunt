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
- **pytest-cov** — coverage reporting. Fail threshold is 100%. Every line is covered; a new one must arrive with the test that reaches it.
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

- **A YAML or JSON document is split at the first list of things it holds.** A sequence of mappings is a list of distinct things and each deserves its own vector; a mapping is the attributes of one thing and splitting it would scatter that thing. Splitting stops at those things, so an entry's own fields do not become chunks. Measured on 40 queries against 110 real specifications: one chunk per test case rather than per file moved top-1 retrieval from **30% to 90%** and MRR from 0.393 to 0.914, with keyword queries that shared no exact string with any stored name. Size was never the problem; a single embedding standing for ten unrelated purposes was.
- **Every parser is wrapped in `SizeLimited`**, in `build_parsers()`, so a chunk no language can shorten is divided on line boundaries before it reaches the embedder. Applying it in one place means a plugin gets it without asking. This was a real gap: the cap lived only in the structured parser, and C and C++ lost 53 percent of their characters past the window — one generated struct held 2,949,177 characters and was read to 8,000. It now splits into 374 pieces.
- **`MAX_CHUNK_CHARS` is 8,000, which is the window the backend actually serves.** Ollama launches an embedding model with `-c 2048`, so a larger cap only means text nobody reads.
- **`MAX_CHUNK_CHARS` is a constant, not a setting.** It describes what the embedding model can read, not what a user prefers. It still applies after the split above, because a single entry can exceed a context on its own. An embedding model reads a fixed number of tokens and drops the rest silently, so a large document indexed whole is mostly unsearchable with nothing to say so. Measured: a 120 KB document and the same document with a distinct tail appended embedded to cosine 1.000000, meaning the tail was never read. `MAX_CHUNK_CHARS` in `adapters/parser/structured.py` is the threshold; above it the parser descends the structure only as far as needed, and warns when a single value still cannot be divided.
- **Markdown and AsciiDoc share one parser.** They differ only in the heading marker and the suffixes, so `MarkupParser` is built twice with different arguments. A section runs to the next heading, its symbol is the heading path, and fenced blocks are skipped so `# comment` in a code sample is not a heading.
- **C and C++ share one parser**, registered as `cpp`, which owns `.h`. The C++ grammar reads nearly all C, and splitting them would leave every header ambiguous. A type is only a definition when it has a body, so `struct Node *next` does not become a second chunk. An attached doc comment travels with the definition, for the same reason decorators do.
- **A declaration is a chunk when it declares a function.** A header is mostly declarations, so without this a realistic header collapses to one class-sized blob: measured, `widget.h` went from 1 chunk to 5. A data member is skipped, because it carries nothing to search for. A prototype is dropped when the same file also defines that symbol, so a `.c` file does not list a function twice.
- Tree-sitter is error tolerant. A partly broken file returns whatever parsed; `ParseError` is raised only when nothing did.

Adding a language is one new module under `adapters/parser/` plus one `PARSERS` entry. It must not require a change to `Scan`, the `Parser` port, or any interface. A parser declares `language` (its identity, stamped onto every chunk it emits) and `suffixes`. Two parsers claiming one suffix is a hard error; resolve it with the `languages` option.

Interfaces call `bootstrap.build_scan(settings)` / `bootstrap.build_search(settings)` and never construct adapters themselves. No dependency injection framework.

## Configuration

`src/ish/settings.py` declares every configurable option once, as fields on the frozen `Settings` dataclass. The CLI builds its flags from those fields and the TOML loader accepts the same names, so the two interfaces cannot drift. Add an option by adding one field — never by editing `args.py`.

Precedence, resolved only in `load_settings()`:

```
defaults < ~/.config/ish/config.toml < ./.ish/config.toml (searched upward) < ISH_* env < CLI flags
```

- **No use case ever receives a `Settings` object.** `Scan` and `Search` take explicit constructor arguments; `bootstrap` reads the settings and passes values. Keep the dependency arrow pointing at values, not at a config bag.
- There is deliberately no way to declare a config-only or CLI-only option. `tests/unit/test_settings.py` enforces the parity in both directions.
- **A config file beside a subtree adds to the one above it.** `project_configs()` returns every file from the path upward, outermost first, and each settles only the keys it names. Reading the nearest alone made a `git = false` for one tree silently drop the `type_patterns` the repository above had set.
- An unknown key warns and is skipped. A malformed or unreadable file raises `ConfigError` and exits 1. Anything that is not a file is absent, so a directory of that name is skipped rather than reported.
- The project config is `.ish/config.toml`, keeping a tree's settings beside anything else the tool leaves there. The older flat `ish.toml` is still read, second, so a file written before this keeps working.
- **A refresh reads the configuration beside each tree, not beside the parent.** An index-scope option decides what belongs in an index, so refreshing a child under the parent's options prunes everything those options reject. A tree that git ignores, kept by a `git = false` of its own, would lose every chunk it holds. `TestRefreshReadsEachTreeConfig` pins this against a real git repository, because git shows tracked files *and* untracked ones no rule covers — the child has to be ignored, not merely untracked, for the parent to reject it.

`src/ish/__init__.py` must stay free of layer imports — `tests/unit/test_package.py` enforces this in a fresh interpreter.

## Indexing

The index persists in SQLite, one file per scanned tree, under `$XDG_CACHE_HOME/ish/`.

- **Vectors are keyed by `(content_hash, model_id)`, not by path.** A renamed file re-embeds nothing, an edited function re-embeds only itself, and switching models keeps both sets.
- **Staleness is two-tier.** Compare `(mtime_ns, size)` first; read and hash only what differs. Never hash every file on every query.
- **Pruning rests on a positive test, never on absence.** A file leaves the index only when it is gone from disk, or when `Scan.accepts()` says the current filter rejects it. Absence from a walk has many causes — an unreadable directory, a race, a narrower root — and removing on absence alone discards a valid index. This was a real defect: a subdirectory turning unreadable for one run silently pruned its files.
- A permission error is treated as "cannot tell" and keeps the entry. Only `FileNotFoundError` counts as gone.
- **Asking inside an indexed tree reads that tree's index.** `find_covering_index()` returns the nearest ancestor index, and `build_search()` narrows the answers to the path asked for. Without it, a search of `30.Firmware/platform` began a second index of 1,596 chunks with nothing reused, because a vector is shared only within one index file. Refreshing from there still prunes only within the path, so the parent keeps everything.
- **Orphans are pruned to the scanned tree only**, so indexing a subdirectory never discards its siblings.
- `remove_files` and `clear` keep vectors, since restoring a file should cost no embedding. `prune_vectors` sweeps unreferenced ones on demand.
- Interfaces must call `Search.close()`, which releases the database.
- **A search of a parent refreshes nothing.** With indexes below it and none of
  its own, the store has no writable primary, so it reads what is stored and
  warns. `bootstrap.refresh_indexes()` visits each tree in turn, with federation
  off so each writes to its own index. Without the warning a stale answer and a
  fresh one look the same; that was a real defect, an integration test index
  left one-fifth built answered every root search for hours.
- **Embedding is bound by tokens, not by cores.** Measured on 14 cores with
  nomic-embed-text over Ollama: 32 chunks of 3 tokens cost 0.8 s, of 50 tokens
  5.3 s, of 1300 tokens 236 s. llama-server takes `-t 14` and still uses two
  cores. Raising `OLLAMA_NUM_PARALLEL` changes nothing, because Ollama pins an
  embedding model to one slot. Throughput is about 270 tokens per second, so
  chunk size, not concurrency, sets the cost of an index.

Measured on this repo (33 files, 104 chunks): a cold index costs ~87s with Ollama, ~51s with llama.cpp, which parallelizes bulk embedding better. A warm query costs ~0.20s.

Measured on a real firmware project (10k files, 30,317 chunks): scanning and parsing the whole tree costs 14s, and embedding is the entire remaining cost at roughly one chunk per second. A whole tree is hours; index the subtrees that matter and let a search of the parent federate over them.

### Indexing a large tree

- **Vectors persist every 64 chunks; chunk rows land at the end of a file.** So a stopped run keeps the expensive half. Restarting a firmware index that had been killed reported `Embedding 6136 new chunks (256 reused)` — content-keyed vectors recovered exactly.
- An index showing `chunks=0` with a positive vector count is a run in progress or one that was stopped, not a corrupt index.
- **The index stores where a chunk is, never what it says.** It holds vectors, paths, line ranges, kinds, and symbol names. A preview reads the file, which also means it always shows the file as it is now.
- Symbol names are stored, and a heading path is a symbol, so prose that appears in a heading is in the index by design. Body text is not.
- **A schema change must vacuum.** Dropping a table frees its pages without clearing them, so an index built when the source was still stored kept that source readable on disk. `TestMigrationErasesOldContent` pins this.
- The lexical half indexes `symbol` and `terms` only. It is gated to identifier-like queries, which match those columns anyway, so dropping the body cost nothing measurable.

Embedding models are often trained with a task prefix for stored text and another for queries, so the `Embedder` port has `embed_documents` and `embed_query` rather than one method. The table lives in `adapters/embedder/prefixes.py`, keyed by model name, because the convention belongs to the model and not to the backend serving it. Measured on this repo with nomic-embed-text over 16 queries: top-1 accuracy 62% without the prefixes, 75% with.

Anything that changes what a stored vector means — the prefixes, or `embed_text()` — must bump `SCHEMA_VERSION` in the SQLite adapter, which discards the old index instead of mixing it.

The default backend is Ollama, reached over HTTP with the standard library. Do not add a client package for it: importing the `ollama` package cost 176ms, which was 71% of a warm query, while the request itself takes ~38ms.

## TUI

`ish -i` is the primary interface. Test it with Textual's pilot in `tests/unit/interfaces/test_tui_app.py`, which drives a real mounted DOM headless. It is not excluded from coverage.

- The query field keeps focus at all times. `up`/`down` and `ctrl+p`/`ctrl+n` move the result highlight through app bindings, and `enter` chooses the highlighted result. Never move focus to the list to navigate it.
- `ENABLE_COMMAND_PALETTE` is off, because Textual binds `ctrl+p` to the palette and would shadow the previous-result key.
- **Filters are written into the query itself**, as `lang:cpp under:/src/`, parsed by `parse_query()`. There is no second input and no focus to manage, which is why the query line carries them. The filter words are stripped before the text reaches the embedder, so a vector is built from what the user wants rather than how they narrowed it. Active filters appear in the header through `sub_title`. A filter with no words left lists everything it allows, without searching.
- Indexing runs on a worker thread and searching on another, so **any store the TUI touches must be safe to use off the thread that opened it**. The SQLite adapter opens with `check_same_thread=False` and guards every statement with one lock. A single-threaded test suite will not catch a regression here; `TestThreadSafety` exists for that.

Measured on one firmware tree, 23,215 chunks over 8 federated indexes: startup 0.77 s, last keystroke to results 0.16 s. It was 0.42 s, and three things paid for the difference.

- **The scored matrix is kept between queries.** Reading every vector out of SQLite and joining 71 MB of blobs cost 118 ms a query while the multiplication cost 2. The cache is dropped when this store writes, and when `PRAGMA data_version` shows another connection has committed, so a second process indexing never leaves a reader stale.
- **The store over-fetches only when something will trim.** A filter and the lexical half both discard, so the wider slice earns its keep there; for a plain query it built 2,311 chunks to return 50.
- **A recent query is not embedded twice.** Typing walks over the same text as characters come and go, so deleting one costs a lookup rather than 79 ms of inference.

**Every thread the interface starts is a daemon, and it owns them itself.**
A thread pool registers an exit hook that joins its threads however it is shut
down, so an embedding in flight held the whole process open: quitting during an
index appeared to hang for as long as the index took. `_DaemonWorker` runs one
call at a time on a daemon thread, and indexing runs on another, so leaving is
immediate. `_leaving` stops a thread talking to an interface that has gone.
`escape`, `ctrl+c`, and `ctrl+q` all quit.

**Searching runs on one thread, and work nobody wants is dropped.** Cancelling
the task that waits on a thread does not stop the thread, so every keystroke's
search used to run to the end: typing 24 characters against a slow backend ran
24 searches, and the query that mattered waited behind all of them. `do_search`
stamps a generation, the search thread checks it before starting, and a single
worker means later work queues rather than racing. The same 24 characters now
run 9. The embedding backend serves one request at a time, so nothing is lost
by serializing.

`tui_debounce_ms` is 120. The debounce is what remains of the latency, so it is a setting rather than a constant.

- **The listing mounts a page, not the index.** `_show_listing()` caps at
  `tui_limit` and names the total in `sub_title`. One widget per chunk costs
  seconds: one firmware tree's 11,543 chunks took 2.4 s before the first keystroke, and
  0.82 s once capped.
- **The query field takes typing from the first frame.** Opening an index of a
  large tree takes most of a second, and a field that cannot be typed into reads
  as an interface that has not started. A query typed while the index opens is
  answered by `_on_index_ready` rather than lost, and the progress message stays
  up meanwhile. Measured on one firmware tree: usable at 0.34 s, against 0.77 s when the
  field waited for the index.
- **A skipped file is counted, not named.** A tree of headers holds many that
  declare nothing: one firmware subdirectory printed 12 warnings before any
  result. `Scan` collects them and reports one line, with the names at `-v`.
  Nothing is swallowed; the count is at WARNING where it will be read.
- **A refresh says which tree it is on.** `--refresh` runs before the interface
  is drawn, so without it the terminal sits blank for minutes and cannot be told
  from a command that has stopped. The CLI writes one line to stderr, rewritten
  in place and cleared at the end, so a piped stdout still holds only results.
- **Report progress while indexing.** A first index of a large tree runs for minutes, and an interface showing a fixed message cannot be told apart from one that has hung. `build_index()` takes a callback; the TUI writes it into the preview pane. This was a real complaint: a schema rebuild left the picker showing "Loading index..." with no sign of the 274 chunks being embedded behind it.

## MCP

`ish-mcp` speaks the Model Context Protocol on stdio. It is the third interface, wired through `bootstrap` exactly like the CLI and TUI, and it offers `search_code`, `list_chunks`, and `index_status`.

- **Nothing may write to stdout except a protocol message.** stdout is the transport; logging goes to stderr. A test asserts every emitted line parses as JSON.
- The protocol layer is hand-written on the standard library. MCP over stdio is newline-delimited JSON-RPC 2.0, which is small enough not to justify a dependency that pulls in pydantic and starlette.
- The server is long-lived, so it holds one `Search` per scanned root and keeps the index warm. That is the whole latency advantage: a query costs a search, not a process start.
- **A resident server re-checks a tree at most every `refresh_seconds`.** An
  editor asks on every character, and `build_index` per call walked the tree —
  for a parent read from the indexes below it, building all 23,215 chunks only
  to count them. That was most of the cost of an answer: a query through Neovim
  took 390 ms and now takes 170.
- A tool failure is reported through `isError`, not a JSON-RPC error, so the host can show the model what went wrong.

Measured: ~58 ms per `search_code` call, against ~190 ms for the same query through the CLI.

### Query cost, and where it goes

Profiled against a real 7,834-chunk index across three federated indexes:

| | |
|---|---|
| process floor (`ish --version`) | 60–80 ms |
| numpy import | 54 ms |
| yaml import | 22 ms |
| build embedder, open indexes | 33 ms |
| embed the query (HTTP to Ollama) | 54 ms |
| **scan every vector** | **48 ms** |
| whole CLI query | ~380 ms |

The scan was 276 ms and grew linearly with the index, which is what made a live editor picker unusable. Scoring the index as one matrix and reading the details of only the winners brought it to 48 ms: the multiplication itself is 1.3 ms, and the rest was materializing rows nobody looked at.

Most of what remains is interpreter and library startup, paid once per process. A resident interface such as MCP pays it at launch and answers in about 130 ms.

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

`include` and `exclude` hold regular expressions, searched against the POSIX path, so `/vendor/` matches at any depth. `exclude` beats `include`, because the safer rule should win a disagreement. A malformed pattern names the option it came from and stops the run.

**Every rule about what to index belongs in `Scan.accepts()` and nowhere else.** Discovery and index pruning both ask that one predicate, so a filter added in only one of them would make pruning delete files it should keep. `test_accepts_agrees_with_discovery` pins this.

### Which settings an interface may override per call

`Settings` fields carry a `scope`. `query_scope_names()` returns the ones an interface may accept for a single call; everything else is index scope and must come from configuration only.

| scope | options | may a call set it? |
|---|---|---|
| query | `lang`, `under`, `type`, `limit`, `no_hybrid` | yes |
| index | everything else | **no** |

The CLI is the exception, and only because a CLI invocation *is* the configuration for that run — a flag there is resolved before anything is built.

For a long-lived interface such as MCP, a call that could set an index-scope option would make the next refresh prune whatever that call excluded: one `search_code` with `languages=["yaml"]` would delete every other language from the index. `TestOnlyQueryScopeIsOverridable` derives the forbidden set from the metadata and fails if a tool ever exposes one, so the rule cannot drift.

`Search.search()` and `all_chunks()` take an optional filter for one call, so narrowing costs no rebuild.

### Two kinds of filter, which must never be confused

| | options | applies to | prunes? |
|---|---|---|---|
| **Index scope** | `include`, `exclude`, `ignore`, `languages`, `git` | what enters the index | yes, through `Scan.accepts()` |
| **Query scope** | `lang`, `under`, `type` | what a search returns | never |

A query-scope filter that reached `Scan.accepts()` would make the next run prune everything it excluded, so `ish --lang markdown` would silently delete every Python chunk. Keep them apart: query filters are built by `build_result_filter()` and passed to the store as the `keep` predicate, applied before the limit so a filtered search still returns a full page. `test_the_filter_does_not_shrink_the_index` pins this.

`Filters` carries the three query-scope narrowings as one value, so adding a
fourth does not widen the signature of every interface that passes them.
`Filters.or_else()` sets the precedence: a filter typed into the query beats a
call argument, which beats configuration. Each interface calls `parse_query()`
on the text it was given, so `type:doc` works the same from the command line,
the TUI, MCP, and Neovim.

`canonical_language()` maps the name a reader types to the name a parser is
registered under, so `lang:c`, `lang:h`, and `lang:cpp` name one parser. A
`Filters` normalizes at construction, which keeps the filter, the display, and
every comparison on one spelling. The CLI derives its `--lang` choices from the
registry plus the alias table, so both interfaces accept the same words.

`type_patterns` lets a repository say what its own paths hold, as
`type:regex`, first match wins, falling back to the built-in reading. A naming
convention belongs to a repository rather than to a language: one firmware tree numbers
its trees, so `20.Tests` and `30.Verification` matched no general rule and
7,395 test chunks were filed as code. `compile_categories()` builds the
function; `bootstrap.build_result_filter()` is the one place that joins it to a
filter, so no interface has to remember.

`type` sorts a chunk with `category_of()` into `code`, `doc`, `test`, or
`config`. The path is consulted before the language, so a YAML fixture under
`tests/` is a test rather than config. The categories partition the corpus:
every chunk has exactly one.

Every listing path applies `build_result_filter()` too — the CLI scan and the MCP `list_chunks` — so a listing and a search never disagree about what is in view.

`--git` is index scope, on by default. It asks git rather than reimplementing ignore rules, so nested `.gitignore` files and global excludes are honored for free. The adapter runs one command per repository and caches the answer, degrades to ignoring nothing outside a repository or when git is unavailable, and is injected into `Scan` as a plain predicate so the application never learns what a version control system is.

Regular expressions rather than globs, deliberately: one matching system keeps `accepts()` a single cheap predicate, and two systems would be two places to keep in step with pruning.

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
