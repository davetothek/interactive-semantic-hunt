# CLAUDE.md — ish project guide

`ish` (Interactive Semantic Hunt) is an interactive semantic search tool for
code, in the style of `fzf`. It is released. This file is the map of the code
and the evidence behind its rules. The rules live in `.claude/rules/`, the
procedures in `.claude/skills/`, and the design under way in `spec.md` at the
root. Read `src/` before a substantial change.

## Where to look

| you want to | read or run |
|---|---|
| know what you must and must not do | `.claude/rules/` — three files load always, the rest when you open a matching file |
| close an issue | `/work-issue N` |
| run every check the way CI does | `/check` |
| add a language or a backend | `/add-language`, `/add-embedder` |
| prove a ranking change kept accuracy | `/benchmark` |
| take the before/after number a commit body needs | `/measure` |
| open a pull request, cut a release | `/pr`, `/release` — only the user invokes these |
| rewrite prose into the house style | `/ste-writing` |

Three hooks run on every matching tool call: `route_model` picks the cheapest
model for a subtask, `guard_git` blocks a push to `main`, a tag push, a forced
push, and every command that discards uncommitted work, and `guard_version`
blocks a version bump outside a release branch. `poe hooks` tests them.

## The four interfaces

| | run with | notes |
|---|---|---|
| CLI | `ish "query" path` | one shot, ~380 ms including interpreter start |
| TUI | `ish -i path` | the primary one, an `fzf`-style picker |
| MCP | `ish-mcp` | resident, ~58 ms a call, for an agent or an editor |
| Python | `from ish.interfaces.python.api import Ish` | holds the index open |

`ish-complete` is a fifth entry point. It finishes a filter word rather than
searching. `Ish` is the session the other three are built on. Nothing beyond
these is built, and an HTTP API stays out by decision.

## Architecture

Hexagonal, ports and adapters. `interfaces → application → domain`, with
`adapters` implementing `application.ports` from below and `bootstrap.py` the
one module that joins them.

| Layer | Path | Purpose |
|---|---|---|
| Domain | `src/ish/domain/chunk.py` | `Chunk`, one named region of a file |
| Domain | `src/ish/domain/match.py` | `Match`, a chunk with its score |
| Composition | `src/ish/bootstrap.py` | Composition root — wiring, selects from the two registries |
| Composition | `src/ish/settings.py` | Option set — one source of truth for CLI flags and TOML keys |
| Port | `src/ish/application/ports/parser.py` | `Parser` Protocol and `ParseError` |
| Port | `src/ish/application/ports/embedder.py` | `Embedder` Protocol |
| Port | `src/ish/application/ports/vector_store.py` | `VectorReader` and `VectorStore` Protocols |
| Application | `src/ish/application/scan.py` | Scan use case: discover, accept, parse |
| Application | `src/ish/application/index.py` | Index use case: staleness, orphans, embedding |
| Application | `src/ish/application/search.py` | Search use case: refresh once, then query |
| Application | `src/ish/application/ranking.py` | The one ranking policy every store runs |
| Application | `src/ish/application/filters.py` | `Filters`, `parse_query()`, the result filter |
| Application | `src/ish/application/categories.py` | `type:` — code, doc, test, config |
| Application | `src/ish/application/languages.py` | Resolve a spelling to a registered language |
| Application | `src/ish/application/progress.py` | `Progress`, what an index run reports |
| Application | `src/ish/application/preview.py` | Read the source a chunk points at |
| Adapter | `src/ish/adapters/parser/__init__.py` | `PARSERS` — every language, and the recipe for adding one |
| Adapter | `src/ish/adapters/parser/python.py` | Python AST parser |
| Adapter | `src/ish/adapters/parser/markup.py` | Markdown and AsciiDoc sections |
| Adapter | `src/ish/adapters/parser/tree_sitter.py` | Tree-sitter parser, C and C++ flavor |
| Adapter | `src/ish/adapters/parser/structured.py` | YAML and JSON documents |
| Adapter | `src/ish/adapters/parser/_limits.py` | `SizeLimited` and `CountLimited`, the chunk size and count caps |
| Adapter | `src/ish/adapters/parser/_plugins.py` | Parsers a user wrote |
| Adapter | `src/ish/adapters/embedder/__init__.py` | `EMBEDDERS` — every backend, and the recipe for adding one |
| Adapter | `src/ish/adapters/embedder/prefixes.py` | `PrefixingEmbedder`, task prefixes, query cache |
| Adapter | `src/ish/adapters/vector_store/sqlite.py` | Persistent vector store (default) |
| Adapter | `src/ish/adapters/vector_store/pure_python.py` | In-memory vector store (`--no-cache`, tests) |
| Adapter | `src/ish/adapters/vector_store/federated.py` | Read several indexes as one |
| Adapter | `src/ish/adapters/vector_store/catalog.py` | Which index file describes which tree |
| Adapter | `src/ish/adapters/vcs/git.py` | Ask git what it ignores |
| Interface | `src/ish/interfaces/python/api.py` | `Ish`, the session every interface shares |
| Interface | `src/ish/interfaces/format.py` | Shared output formatting |
| Interface | `src/ish/interfaces/log.py` | Logging setup, shared by every entry point |
| Interface | `src/ish/interfaces/completion.py` | Finish a filter word |
| Interface | `src/ish/interfaces/cli/args.py` | Argument parsing, derived from `Settings` |
| Interface | `src/ish/interfaces/cli/main.py` | CLI entry point |
| Interface | `src/ish/interfaces/cli/complete.py` | `ish-complete` entry point |
| Interface | `src/ish/interfaces/tui/app.py` | Textual TUI (`ish -i`) |
| Interface | `src/ish/interfaces/mcp/protocol.py` | MCP stdio JSON-RPC transport |
| Interface | `src/ish/interfaces/mcp/server.py` | MCP tools (`ish-mcp`) |

Languages: `python`, `markdown`, `asciidoc`, `cpp`, `yaml`, `json`. Backends: `ollama` (default), `llama.cpp`, `st`.

## Tooling

`uv` manages the environment and `uv.lock`. `ruff` lints and formats, `ty` checks
types, `pytest-cov` holds the suite at 100 %, and `poethepoet` names the tasks.

| Command | What it does |
|---|---|
| `poe test` | pytest with coverage, fails under 100 % |
| `poe hooks` | the hook tests under `.claude/hooks/tests` |
| `poe lint`, `poe format` | ruff over `src`, `tests`, `scripts`, `.claude/hooks` |
| `poe typecheck` | ty over `src` and `scripts` |
| `poe check` | lint → typecheck → test → hooks |
| `poe release X.Y.Z` | the version bump on its own branch |

The CLI prints one line per chunk: `src/foo.py:12-27  function  parse_config`.

## The evidence

Each rule in `.claude/rules/` exists because something was measured or
something broke. This is that record, so a rule is never argued from taste.

### Chunking

- A YAML or JSON document is split at the first list of things it holds.
  Measured on 40 queries against 110 real specifications: one chunk per test
  case instead of per file moved top-1 retrieval from 30 % to 90 % and MRR
  from 0.393 to 0.914. Size was never the problem. One embedding standing for
  ten unrelated purposes was.
- `SizeLimited` wraps every parser in one place. Before that the cap lived
  only in the structured parser, and C and C++ lost 53 % of their characters
  past the window. One generated struct held 2,949,177 characters and was
  read to 8,000. It now splits into 374 pieces.
- `MAX_CHUNK_CHARS` is 8,000 because Ollama launches an embedding model with
  `-c 2048`. A model reads a fixed number of tokens and drops the rest with
  no signal: a 120 KB document and the same document with a distinct tail
  embedded to cosine 1.000000.
- A header is mostly declarations. Emitting a chunk for a function
  declaration took `widget.h` from 1 chunk to 5.
- One generated JSON register map of 26.3 MB produced 32,768 chunks in 76 s.
  One index run logged 12,795 warnings for values that could not be divided,
  which is why the parser now reports once per file.

### Indexing

- Pruning on absence from a walk was a real defect: a subdirectory that
  turned unreadable for one run silently pruned its files.
- An unparseable file that was never stamped was read again on every
  refresh, for ever, while `files_parsed` read as a clean no-op.
- Opening an index used to write to it. A refresh of four indexes wrote to
  all four with no file changed, that write was the second writer in a stall
  that ran for minutes, and it moved `PRAGMA data_version`, which cost every
  reader a 118 ms matrix rebuild per index.
- A search of `30.Firmware/platform` once began a second index of 1,596
  chunks with nothing reused, because a vector is shared only within one
  index file. Hence the covering-index lookup.
- A parent that refreshed nothing and said nothing served an integration
  test index left one-fifth built as the answer to every root search for
  hours. Hence the warning.
- Refreshing a child under the parent's options pruned everything the parent
  rejected. `TestRefreshReadsEachTreeConfig` pins it against a real git
  repository, because the child has to be ignored, not merely untracked.
- Dropping a table without a vacuum kept the old source text readable on
  disk. `TestMigrationErasesOldContent` pins it.
- A stopped run keeps the expensive half. Restarting a killed firmware index
  reported `Embedding 6136 new chunks (256 reused)`.
- Embedding is bound by tokens, not by cores. On 14 cores with
  nomic-embed-text: 32 chunks of 3 tokens cost 0.8 s, of 50 tokens 5.3 s, of
  1300 tokens 236 s. Ollama pins an embedding model to one slot, so
  `OLLAMA_NUM_PARALLEL` changes nothing. About 270 tokens per second.
- The request batch is how long a search waits during an index run. Over 64
  definitions: 97 s at a batch of 64, 23 s at 16, 12 s at 8, while the whole
  run cost 97.2, 95.8, and 96.9 s. Vectors are bit-identical at every size,
  verified over 32 texts from 17 to 8,409 characters. The batch is 8.
- Under WAL a reader is never blocked by a writer. Against a writer
  committing 168k vectors, a second process opened in 0.4 ms, searched in
  120 ms, and listed chunks in under 1 ms. When a search feels blocked, look
  at the embedding queue.
- An index run once waited on a dead socket for 10 hours while the daemon
  stayed healthy. Hence the request timeout and the retry.
- A call against an index under a writer waited without a limit. One run
  held `index_status` for 1800 s, and the client gave up with no error. A
  store now waits 2 s for the lock and names the file. A status call also
  stopped bringing the index up to date first, which is what it was waiting
  for.
- Cold index of this repo (33 files, 104 chunks): ~87 s with Ollama, ~51 s
  with llama.cpp. A real firmware project (10k files, 30,317 chunks): 14 s to
  scan and parse, then about one chunk per second to embed. A whole tree is
  hours. Index the subtrees that matter and federate.

### Embedding

- Task prefixes: measured on this repo with nomic-embed-text over 16 queries,
  top-1 accuracy 62 % without, 75 % with.
- Importing the `ollama` package cost 176 ms, 71 % of a warm query, while the
  request itself takes ~38 ms. Hence `urllib`.

### Ranking

- The result filter used to run after the top slice. `--type code` returned
  nothing at a limit of 20 and two results at 100, because no code chunk sat
  in the slice.
- The `is_code_like` gate, measured on this repo over 20 queries:

  | ranking | conceptual | identifier | combined |
  |---|---|---|---|
  | vector only | 90 % / MRR .925 | 90 % / MRR .910 | 90 % / MRR .918 |
  | hybrid, always on | 80 % / MRR .883 | 90 % / MRR .950 | 85 % / MRR .917 |
  | hybrid, gated (shipped) | 90 % / MRR .925 | 90 % / MRR .950 | 90 % / MRR .938 |

  Fusing a lexical order into a plain description costs 10 points of top-1.
  A 3:1 weight sweep still lost 5. The gate is not a nicety.
- Symbol names and a heading path are indexed for the lexical half. Dropping
  the body cost nothing measurable.

### Latency

Profiled against a real 7,834-chunk index across three federated indexes:

| | |
|---|---|
| process floor (`ish --version`) | 60–80 ms |
| numpy import | 54 ms |
| yaml import | 22 ms |
| build embedder, open indexes | 33 ms |
| embed the query (HTTP to Ollama) | 54 ms |
| scan every vector | 48 ms, was 276 ms |
| whole CLI query | ~380 ms |

- The scan grew linearly with the index. Scoring it as one matrix and reading
  only the winners brought 276 ms to 48 ms. The multiplication is 1.3 ms.
- On 23,215 chunks over 8 federated indexes: TUI startup 0.77 s, keystroke to
  results 0.16 s, was 0.42 s. Three things paid: the scored matrix is kept
  between queries (reading 71 MB of blobs cost 118 ms a query), the store
  over-fetches only when something trims (a plain query built 2,311 chunks
  to return 50), and a recent query is not embedded twice (a backspace costs
  a lookup, not 79 ms).
- Typing 24 characters against a slow backend once ran 24 searches. With a
  generation stamp it runs 9.
- One widget per chunk cost 2.4 s before the first keystroke on 11,543
  chunks. Capped at a page, 0.82 s.
- The query field usable at 0.34 s against 0.77 s when it waited for the
  index.
- `ish-complete` costs ~107 ms a Tab, nearly all interpreter start.
- MCP: ~58 ms per `search_code`, against ~190 ms for the same query through
  the CLI. A resident server answers in about 130 ms. `build_index` per call
  once cost 390 ms through Neovim against 135 now.
- Returning a table to fzf-lua stopped Neovim redrawing for 160 ms a
  keystroke.

### Interfaces

- A thread pool held the process open for as long as an index took, because
  it joins its threads at exit. Hence daemon threads the TUI owns.
- A schema rebuild once left the picker at "Loading index..." with 274
  chunks embedding behind it. Hence the progress callback.
- One firmware subdirectory printed 12 header warnings before any result.
  Hence one counted line.
- One firmware tree numbers its directories, so 7,395 test chunks were filed
  as code until `type_patterns` let a repository say what its paths hold.
- Reading only the nearest config file made a `git = false` for one tree
  drop the `type_patterns` the repository above had set.
