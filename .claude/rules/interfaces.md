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
  end. stdout holds only results.
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

## Neovim

- The picker never waits on the main loop. Return a function to fzf-lua, not
  a table.
