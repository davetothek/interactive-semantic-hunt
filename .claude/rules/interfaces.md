---
paths:
  - "src/ish/interfaces/**"
  - "contrib/**"
---

# Interfaces

There are four: CLI, TUI, MCP, and the Python `Ish` session. `ish-complete`
finishes a filter word. Nothing else is added.

## Every interface

- Go through `Ish`. It calls `bootstrap.build_scan()` and
  `bootstrap.build_search()`. Do not construct an adapter in an interface.
- Call `parse_query()` on the text you were given, so `lang:cpp type:doc`
  works the same everywhere.
- Accept only query-scope options per call: `lang`, `under`, `type`, `limit`,
  `no_hybrid`, `ask_turns`. `TestOnlyQueryScopeIsOverridable` derives the forbidden set
  from the settings metadata. The CLI is the exception, because a CLI call is
  the configuration for that run.
- Apply `build_result_filter()` to every listing as well as every search.
- Call `Search.close()`.
- Report indexing progress through the `Progress` callback. A run of minutes
  with a fixed message cannot be told from a hang.

## CLI

- Output one line per chunk: `path:start-end  kind  symbol`.
- `ish chat` and `ish remote chat` run one loop, `cli/chat.py`, over the
  `Chat` use case. Each reply goes through `ask` with a `Reading`, so the
  grounding of an answer holds for each turn. The history never reaches
  disk, or the model as a source. Only the parts of an answer that cite a
  chunk enter it. A server gets a query of the last question with the new
  one, as it gets any query, and never an answer.
- Write the refresh line to stderr, rewritten in place and cleared at the
  end. stdout holds only results. The line lives in `interfaces/busy.py`,
  the one place a step is rendered with a rate and a time left, and
  `ish-serve` writes the same text into its status.
- Count skipped files in one line at WARNING. Name them at `-v`.
- `--color` decides color for each stream apart: `auto` colors a terminal
  unless `NO_COLOR` is set.
- An answer is plain Markdown on every stream, so a copy from the terminal
  is the text that a pipe gets. Only `--render` shows it rendered, and only
  on a terminal, through `cli/terminal.py`. A chat is the exception: it
  renders each reply on a terminal, with its sources in grey, and marks a
  citation as `[n]` where an answer writes `[^n]`.
- Import Rich only inside the function that renders an answer. A search
  and a plain answer must not load it.

## TUI

- The query field keeps focus at all times. Navigate the list through app
  bindings, never by moving focus.
- `ENABLE_COMMAND_PALETTE` stays off. It would shadow `ctrl+p`.
- Filters are written into the query itself. There is no second input.
- Every thread the TUI starts is a daemon, and the TUI owns it. Never use a
  thread pool. Quitting must be immediate.
- Searching runs on one worker. Stamp a generation on each search and drop
  work nobody wants.
- Mount a page of the listing, capped at `tui_limit`, and name the total.
- Test with Textual's pilot. The TUI is not excluded from coverage.

## MCP

- Nothing writes to stdout except a protocol message. Logging goes to
  stderr. A test asserts every emitted line parses as JSON.
- The protocol layer is hand-written on the standard library. Do not add an
  MCP package.
- Hold one `Search` per root. Refresh on a thread, never on the way to an
  answer. `refresh_index` wakes the thread and returns at once.
- `index_status` reports what the index holds. It never builds one.
- An index another process holds is left out of a search above it, with a
  warning that names it. A search of the held tree itself raises `StoreBusy`.
- Report a tool failure through `isError`, not a JSON-RPC error.
- `ish-serve` fills the index and answers nothing. It refreshes its tree
  and every index of its repository in any checkout, from the start, then
  on the interval or when nudged. It writes its status under the runtime
  directory every few seconds, steps aside when a live server already
  reaches its tree, and stops when its tree is gone. Nothing connects a
  client to it.
- A client reads the status through `Ish.kept_by()`. A server reaches a
  path from the same repository, wherever the checkout is, or from a tree
  above it. With a live one, `Ish.index()` reads what is stored,
  `IshTools` starts no watch thread, and `refresh_index` touches the nudge
  file. A status nobody has written for `STALE_SECONDS` is a dead server,
  and the client refreshes itself again. Editor clients know nothing of
  the status file.
- `ish-mcp` serves stdio by default. `ish-mcp --http HOST:PORT` serves the
  same tools over MCP Streamable HTTP at `/mcp`. Each request carries a
  bearer token that a method of `auth` admits, or a login token, through
  the `Access` of `bootstrap.build_access()`. The transport knows no
  method. `/login` beside `/mcp` lists the sign-in methods and signs a
  user in. No log line holds its body. Over
  HTTP it offers
  `sources` in place of `ask`. It returns chunks with their text, and the
  server builds no language model. A transport in
  `interfaces/mcp/transport.py` frames messages and nothing else, so a new
  one changes no tool. A REST API stays out.

## A server

- `ish remote`, with `ish remote mcp`, goes through `RemoteIsh`, and
  nothing else does. It gets
  its chunks from `bootstrap.build_chunk_source()` and answers through the
  `AnswerQuestion` that a local answer uses.
- Build the language model before the first request. A machine with no
  allowed model reads no text of the server.
- Send a filter only when the call gives it. A filter from the
  configuration of this machine is for a tree of this machine.
- A line without the word `remote` reads no server. `--remote URL` with
  `--ask` or `--brief` is the one exception, and it warns.
- `ish remote mcp` offers every tool that reads the server, with the
  schemas of the local tools: `search_code`, `ask`, `list_chunks`,
  `index_status`, and `complete_filter`. `refresh_index` changes the
  server, so it does not travel, and `ChunkSource.read` refuses it. A
  call with a `path` is refused. `ish mcp` takes no option that names a
  server.
- `ish remote login` asks for the password with no echo, only after
  `password_problem()` allows it, and keeps no password. A command of
  `ish remote` sends `ISH_MCP_TOKEN`, or else the login token of the URL.
- A server with a sign-in method listens on a loopback address only.
  `server.main` refuses another before it serves.
- Each tool call over HTTP writes one line to the `ish.audit` logger: the
  user and the tool, never the arguments. `ish mcp --http` shows it at
  every verbosity.
- A local search and a local `ish mcp` import no chunk source and no
  `remote` module. `tests/unit/interfaces/cli/test_run.py` checks it in a
  fresh interpreter.

## Neovim

- The picker never waits on the main loop. Return a function to fzf-lua, not
  a table.
- Both editor clients choose the source the same way: `auto` reads the
  local index when `index_exists` finds one, the server when `ish remote
  mcp` starts, and both when both do. A question runs `ish ask`, and offers
  `ish remote ask` when the local answer is not covered.
- An editor asks once a tree before it builds an index, and keeps the
  answer for the session. A save refreshes only a tree chosen here.
- `index_exists` starts no index and no refresh, and creates no index
  file. `Ish.stored()` reads the catalog alone, because opening an index
  that does not exist creates it. An index that exists is opened to count
  it, as a search opens it.
