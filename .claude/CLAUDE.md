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

`ish-complete` and `ish-serve` are two more entry points, not interfaces.
The first finishes a filter word rather than searching. The second keeps
the indexes of a tree current and answers no query: it writes a status
file the clients read, and a client that finds a live one refreshes
nothing itself. `Ish` is the session the other three are built on. Nothing
beyond these is built. `ish-mcp --http` serves the MCP tools over HTTP
behind a bearer token, and a REST API stays out by decision.

`ish --ask` and the `ask` MCP tool answer a question from the chunks a
search finds, with citations. A language model writes the answer, and
only a backend that `allowed_language_models` names is ever built.

## Architecture

Hexagonal, ports and adapters. `interfaces → application → domain`, with
`adapters` implementing `application.ports` from below and `bootstrap.py` the
one module that joins them.

| Layer | Path | Purpose |
|---|---|---|
| Domain | `src/ish/domain/chunk.py` | `Chunk`, one named region of a file |
| Domain | `src/ish/domain/match.py` | `Match`, a chunk with its score |
| Domain | `src/ish/domain/location.py` | `Location`, where a chunk sits: lines, pages, slides, cells, sections |
| Composition | `src/ish/bootstrap.py` | Composition root — wiring, selects from the three registries |
| Composition | `src/ish/settings.py` | Option set — one source of truth for CLI flags and TOML keys |
| Composition | `src/ish/runtime.py` | The directory where the processes of one user share files while they run |
| Port | `src/ish/application/ports/parser.py` | `Parser` Protocol and `ParseError` |
| Port | `src/ish/application/ports/embedder.py` | `Embedder` Protocol |
| Port | `src/ish/application/ports/vector_store.py` | `VectorReader` and `VectorStore` Protocols |
| Port | `src/ish/application/ports/language_model.py` | `LanguageModel` Protocol, `Source`, `Reply` |
| Port | `src/ish/application/ports/link.py` | `LinkResolver` Protocol |
| Application | `src/ish/application/scan.py` | Scan use case: discover, accept, parse |
| Application | `src/ish/application/index.py` | Index use case: staleness, orphans, embedding |
| Application | `src/ish/application/search.py` | Search use case: refresh once, then query |
| Application | `src/ish/application/ranking.py` | The one ranking policy every store runs |
| Application | `src/ish/application/filters.py` | `Filters`, `parse_query()`, the result filter |
| Application | `src/ish/application/categories.py` | `type:` — code, doc, test, config |
| Application | `src/ish/application/languages.py` | Resolve a spelling to a registered language |
| Application | `src/ish/application/progress.py` | `Progress`, what an index run reports |
| Application | `src/ish/application/preview.py` | Read the source a chunk points at |
| Application | `src/ish/application/answer.py` | Answer use case: search, read, fit the budget, ask the model |
| Adapter | `src/ish/adapters/parser/__init__.py` | `PARSERS` — every language, and the recipe for adding one |
| Adapter | `src/ish/adapters/parser/python.py` | Python AST parser |
| Adapter | `src/ish/adapters/parser/markup.py` | Markdown and AsciiDoc sections |
| Adapter | `src/ish/adapters/parser/tree_sitter.py` | Tree-sitter parser, C and C++ flavor |
| Adapter | `src/ish/adapters/parser/structured.py` | YAML and JSON documents |
| Adapter | `src/ish/adapters/parser/_limits.py` | `SizeLimited` and `CountLimited`, the chunk size and count caps |
| Adapter | `src/ish/adapters/parser/_plugins.py` | Parsers a user wrote |
| Adapter | `src/ish/adapters/embedder/__init__.py` | `EMBEDDERS` — every backend, and the recipe for adding one |
| Adapter | `src/ish/adapters/embedder/prefixes.py` | `PrefixingEmbedder`, task prefixes, query cache |
| Adapter | `src/ish/adapters/embedder/turn.py` | The marker a query leaves, and the wait a document batch takes |
| Adapter | `src/ish/adapters/language_model/__init__.py` | `LANGUAGE_MODELS` — every backend, and the recipe for adding one |
| Adapter | `src/ish/adapters/language_model/anthropic.py` | Messages API with native citations |
| Adapter | `src/ish/adapters/language_model/ollama.py` | A generation model on the local daemon |
| Adapter | `src/ish/adapters/language_model/claude_cli.py` | The Claude Code CLI in print mode |
| Adapter | `src/ish/adapters/language_model/_grounded.py` | Schema and parser for a backend without citations |
| Adapter | `src/ish/adapters/language_model/prompts.py`, `prompts/*.md` | The instructions a model receives, as files `prompt_dir` replaces |
| Adapter | `src/ish/adapters/link/file_uri.py`, `base_url.py` | Link a chunk to its file, or to a published tree |
| Adapter | `src/ish/adapters/vector_store/sqlite.py` | Persistent vector store (default) |
| Adapter | `src/ish/adapters/vector_store/pure_python.py` | In-memory vector store (`--no-cache`, tests) |
| Adapter | `src/ish/adapters/vector_store/federated.py` | Read several indexes as one |
| Adapter | `src/ish/adapters/vector_store/catalog.py` | Which index file describes which tree |
| Adapter | `src/ish/adapters/vcs/git.py` | Ask git what it ignores |
| Interface | `src/ish/interfaces/python/api.py` | `Ish`, the session every interface shares |
| Interface | `src/ish/interfaces/format.py` | Shared output formatting |
| Interface | `src/ish/interfaces/log.py` | Logging setup, shared by every entry point |
| Interface | `src/ish/interfaces/busy.py` | The busy line: spinner, step, rate, and time left |
| Interface | `src/ish/interfaces/heartbeat.py` | The status file of `ish-serve`, and the nudge beside it |
| Interface | `src/ish/interfaces/daemon.py` | `ish-serve`, the one process that fills an index |
| Interface | `src/ish/interfaces/completion.py` | Finish a filter word |
| Interface | `src/ish/interfaces/cli/args.py` | Argument parsing, derived from `Settings` |
| Interface | `src/ish/interfaces/cli/main.py` | CLI entry point |
| Interface | `src/ish/interfaces/cli/complete.py` | `ish-complete` entry point |
| Interface | `src/ish/interfaces/tui/app.py` | Textual TUI (`ish -i`) |
| Interface | `src/ish/interfaces/mcp/protocol.py` | MCP JSON-RPC dispatch |
| Interface | `src/ish/interfaces/mcp/transport.py` | MCP framing: stdio, and HTTP behind a token |
| Interface | `src/ish/interfaces/mcp/server.py` | MCP tools (`ish-mcp`) |

Languages: `python`, `markdown`, `asciidoc`, `cpp`, `yaml`, `json`. Backends: `ollama` (default), `llama.cpp`, `st`.
Language models: `anthropic`, `ollama`, `claude-cli`, none by default.

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
  embedded to cosine 1.000000. `context_tokens` scales the cap and reaches
  the backend. nomic-embed-text accepts 8192. The retrieval gain of the
  wider window is not yet measured, so the default stays at 2048.
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
- A search of `core/platform` once began a second index of 1,596
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
- A matrix file stamped with row counts alone cannot tell a replaced file:
  swapping the vectors behind two chunks leaves every count the same, and
  SQLite's own change counter does not move in WAL mode. Hence the
  `generation` row in `meta`, bumped by every write transaction.
- On /srv/mono, the firmware tree of two worktrees, 6260 and 6266 chunks,
  shares 100 % of its chunk hashes with the main index: 0 to embed, 0.9 s
  to parse. A vector was shared only inside one index file, so each would
  have embedded for hours. Hence one vectors store per repository, keyed by
  the common directory every worktree reports alike. Moving 81,958 vectors
  of the 7 main indexes into it took one query of 2.4 s, and the
  testing index went from 163 MB to 19 MB.
- A scan rooted under `.claude/worktrees/` found 0 of 6260 chunks while
  `ignore` and `exclude` judged the whole path. Judged from the scan root
  instead, the main firmware tree grew from 6266 to 8152 chunks, because
  patterns that name the tree, such as `core/include/gen`, stopped
  matching when that tree was the one scanned. Hence the top of the
  checkout as the anchor, which gives 6260 and 6266.
- A worktree index under /srv/mono joined every root search and nested
  under the main tree, which put the count of a root query back on the
  listing path. Hence a search reads the indexes of its own checkout.

### Answering

- A stored index holds where a chunk is, not what it says. On the first
  real question against a tree of 74,737 chunks, retrieval put the
  expected section first and second, and the model still said the
  documents did not cover it: every chunk had empty text, and the model
  read 696 tokens of titles for ten sources. Read from the file, the same
  question read 2,867 tokens and cited the expected section. Hence
  `read_lines` in the answer use case.
- Ollama starts a model with a window of a few thousand tokens and drops
  the rest of a prompt with no signal. An answer may send 60,000
  characters. Hence `num_ctx` of 20,480 on every request.
- A model with no citation feature cited a source by its title instead of
  its id, which the use case drops. Naming the id as the bare number in
  the prompt fixed it on claude-haiku-5-5 and qwen2.5-coder:7b.
- First scored run of `ask`, 25 questions over a private specification
  tree of 74,737 chunks, claude-sonnet-5-5 through claude-cli: the
  expected section cited for 14 of 21 answerable questions, all 4
  unanswerable ones refused, 5.4 s and 8,126 input tokens a question,
  median. Six of the seven misses never reached the model: the expected
  section was not in the ten chunks retrieved. Given the section, the
  model cited it 14 times in 15. Retrieval is the limit, not the model.
  claude-haiku-5-5 scored the same 14, question by question.
- Recall over the same 21 questions: 16 at 10 chunks, 20 at 30, 21 at 50.
  The expected spec ranked 15th to 32nd behind test cases that share the
  wording. A cap per file reached 17. A rerank by claude-haiku-5-5 of the
  top 50, from the first 300 characters of each, put all 21 in its ten:
  21 of 21 cited, 21 of 21 answered, 4 of 4 refused, in three runs out of
  three. 13,984 rerank and 7,639 answer input tokens, 11.6 s, median.
  At 30 candidates it fell to 19, because one source ranked 32nd. Hence
  `rerank_model` and 50 `rerank_candidates`.
- Follow-up searches, the same 25 questions, rerank and claude-haiku-5-5,
  `ask_turns = 3`: 19 answered in one turn. The model searched again for
  2 hard questions and for all 4 uncovered ones, and still refused the 4.
  20 of 21 cited, the miss the q07 label that accepts one of several
  sources. 14.8 s against about 13, 0.4 cents against 0.3 an answer. An
  agent that greps the tree found a firmware handler that ish cannot,
  for 211k input tokens, because the handler is one case of a switch
  that one chunk holds whole.
- A register table that a specification assembled with `include::` held
  the directives and none of the values, so the question "which registers
  control the watchdog timer, with reset values" found the names and no
  values. 21,820 includes over 2,488 AsciiDoc files, 90 % through an
  attribute path. With includes resolved, the same question found all 13
  registers with precision and reset value in the first search, where it
  had needed three follow-up turns. A register table split by the cap kept
  its header only in the first piece, and one table holds one embedding of
  up to a dozen registers. The re-index embedded 6,841 chunks in 85 minutes,
  about 1.3 a second, and the other trees parsed again in under a minute.
  The 25 questions scored 20 of 21 cited, 21 of 21 answered, 4 of 4
  refused, the miss a label that names one section of two valid ones.
  Answer input rose from 9,839 to 16,203 tokens, output from 2,747 to
  4,257, 0.43 to 0.5 cents. Hence `_asciidoc.py`.
- The miss was the cost of resolving includes into prose. A section of
  1,109 characters of prose and four includes grew to 15,417, its first
  chunk mostly register rows, and it fell from the top 50 to rank 141 for
  the question its prose answers. With an include of more than 600
  characters split into a `table` chunk of its own, the 25 questions
  scored 21 of 21 cited, 21 of 21 answered, 4 of 4 refused, the register
  question still found its 13 rows in the first search, and an answer read
  9,388 input tokens and wrote 3,254, 0.45 cents. Hence `INLINE_CHARS`.
- `refresh_indexes()` reads the configuration beside each tree again, and
  only *overrides* survive it, so a `cache_dir` set on the settings it is
  given is lost and the run writes to the default index. A test run that
  meant to fill a copy refreshed the index a resident server kept.
- `claude -p` run inside a tree loaded the auto memory of that tree with
  `--setting-sources ""`, and on 2.1.296 also with
  `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`:
  5,534 input tokens either way. Run in an empty directory, it loaded none,
  2,300 tokens. A managed policy of an organization still loads. Hence an
  empty directory for every call, and the Anthropic API for a server.
- `claude --bare` skips the login as well as the hooks. The claude-cli
  backend turns off settings, tools, and MCP servers one by one instead,
  and runs in an empty directory.

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
- On /srv/mono, 7 sibling indexes, 9,231 files, 72,995 chunks, 224 MB of
  vectors, a root query through the CLI, cold, median of 5: 1110 ms → 629 ms
  when the federated count became a sum of counts rather than a listing of
  73,000 chunks; 670 ms → 213 ms when the matrix came from a mapped file
  beside each index rather than from the blobs. The mapped file loads in
  18 ms against 310 to 460 ms for the blobs, and the first run after a
  change writes it in about 1.2 s.
- A query inside the firmware tree of 6,266 chunks walked the tree before
  it answered: 691 ms cold, median of 5. Beside an `ish-serve` that keeps
  the tree it reads what is stored, 274 ms. At the root, which never
  walked, 323 ms against 329 ms. `index_status` at the root built 74,459
  chunks to count them by language, 981 ms a call, and a repeated call
  costs 2 ms while the count and the last finish of the server stand.
- Ollama serves one embedding request at a time. Beside an index run
  embedding 64 chunks of about 250 tokens in requests of 8, a picker
  keystroke 0.3 s after the last cost 605 ms, median of 5 trials, against
  15 ms beside an idle daemon. With the marker a query leaves and the run
  waits for between requests, 23 ms. The first keystroke still waits for
  the request in flight, 601 ms, and the run of 64 chunks takes 11.0 s
  against 7.2 s, the time the person typed plus the 2 s hold. One text a
  request takes the first keystroke to 115 ms beside chunks of about 250
  tokens, and to 45 ms, worst 135 ms, beside chunks of about 1000 tokens,
  because the daemon reads a long text in pieces. The run pays for it on small
  chunks: 64 chunks of about 250 tokens embed in 9.1 s one at a time against
  7.5 s in requests of 8, 16 chunks of about 1000 tokens in 9.3 s either
  way, and alone or in a request of 8 the vectors differ by 0.0.
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
- One socket server for every client of /srv/mono serialized them. A
  refresh asked for by one client embedded 696 chunks inside the server,
  342 s in all, while the picker polled `index_status` every second at
  1.2 to 1.9 s a call, and each query embedding waited behind a document
  batch of the one Ollama slot. Searches took 3 to 9 s against 128 to
  279 ms in-process. Hence `ish-serve` fills the index and answers
  nothing, and every client reads the index itself.
- A server refreshed only the indexes under its path, and a client found
  one only by walking up from the path it asked about. A worktree inside
  the main checkout walked its 669 files on every run beside a server
  that never touched it, and a worktree beside the checkout, where git
  puts one unless told otherwise, could not reach that server at all.
  Hence one server per repository, and clients that read every status
  under the runtime directory. A server whose tree was deleted went on,
  pruned every file from the index, and wrote its status for ever. Hence
  a server stops when its tree is gone.
