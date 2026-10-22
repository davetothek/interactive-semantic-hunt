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
  `no_hybrid`. `TestOnlyQueryScopeIsOverridable` derives the forbidden set
  from the settings metadata. The CLI is the exception, because a CLI call is
  the configuration for that run.
- Apply `build_result_filter()` to every listing as well as every search.
- Call `Search.close()`.
- Report indexing progress through the `Progress` callback. A run of minutes
  with a fixed message cannot be told from a hang.

## CLI

- Output one line per chunk: `path:start-end  kind  symbol`.
- Write the refresh line to stderr, rewritten in place and cleared at the
  end. stdout holds only results. The line lives in `interfaces/busy.py`,
  the one place a step is rendered with a rate and a time left, and
  `ish-serve` writes the same text into its status.
- Count skipped files in one line at WARNING. Name them at `-v`.

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
- `ish-mcp` serves stdio and nothing else.

## Neovim

- The picker never waits on the main loop. Return a function to fzf-lua, not
  a table.
