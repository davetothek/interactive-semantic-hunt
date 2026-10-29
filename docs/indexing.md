# Index

The index persists in SQLite under `$XDG_DATA_HOME/ish/`, one file per scanned
tree. **A search of a parent reads the indexes below it and refreshes none** —
choosing one of them to write to would be wrong — so it warns and offers
`--refresh`, which visits each tree in turn. A repeated query reuses it, so only changed files are parsed and only new
text is embedded. A renamed file re-embeds nothing.

Beside each index file, a `-matrix` file holds the vectors of the model in
use. A search maps that file into memory instead of reading every vector out
of SQLite, which is what a fresh process used to pay first. A file older than
the index is read past and written again, so it can be deleted at any time.

Each index records the tree it was built from, so searching a directory also
searches every index below it. Index the parts of a large project separately and
search the whole from its root:

```sh
ish "warm" project/docs        # index one part
ish "warm" project/firmware    # and another
ish "how is exposure set" project    # searches both
```

When a release changes how files divide into chunks, the next refresh
reads every file again under the new division and embeds only text it has
never seen. Parsing a 10,000-file tree costs seconds. Embedding it costs
hours, and a vector is keyed by its text, so none of that is paid twice.

Searching a parent never rewrites an index below it. Pass `--no-federate` to use
only the index of the exact path.

## Keep the index current

`ish-serve` keeps the indexes of a tree current, for every client that
reads them. It refreshes the tree as soon as it starts, then every
`refresh_seconds`, 30 by default, or when a client asks, and it answers no
query:

```sh
ish-serve /path/to/tree
```

The server writes a status file under `$XDG_RUNTIME_DIR/ish/`, named after
the tree the way its index file is, every few seconds while it runs. Every
client reads the index itself. It walks up from the path it asks about, and
when it finds the status of a live server whose refresh reaches that path,
it leaves the refreshing to the server and reads what is stored:

- `ish "query" path` walks nothing and embeds nothing before it answers.
  Measured on a firmware tree of 6,266 chunks, a cold query took 691 ms on
  its own and 274 ms beside a server, the difference being the walk of the
  tree. While the server is mid-refresh, the run says so on stderr, with
  the step the server is at, how fast it embeds, and about how long is
  left, because the answer may not hold the latest edit yet.
- `ish-mcp` starts no refresh thread for a tree the server keeps, and
  `refresh_index` asks the server for a refresh now. `index_status` reports
  what the server is doing.
- Nothing in the editor clients changes.

A search goes first. Ollama serves one embedding request at a time, so a
query that arrives while the server embeds waits for the text in flight, and
for nothing else: the server sends one text a request, the query leaves a
marker under the runtime directory, and the server holds its next request
back for 2 seconds after the last query. Measured beside a server embedding
chunks of about 250 tokens while a picker types 0.3 s apart, the first
keystroke costs 115 ms and every keystroke after it 23 ms, which is what a
query costs beside an idle daemon. Beside chunks of about 1000 tokens the
first keystroke costs 45 ms, since the daemon reads a long text in pieces
and the query slips in between them. One text a request costs the server about
a fifth of its throughput on small chunks and nothing on large ones, and
changes no vector.

Without a server everything works as before: `ish` brings the index up to
date in front of the answer, behind a line that shows what it is doing, and
`ish-mcp` refreshes on a thread of its own. A server that was killed leaves
a status file nobody writes, and after 30 seconds the clients read past it
and refresh themselves again. A second `ish-serve` for the same tree exits
at once, so a shell hook may start one without checking first, and one that
finds the status of a dead server takes its place.

One server keeps a whole repository current: every index at or below its
tree, and every index of another checkout of the same repository, wherever
that worktree is on disk. A second `ish-serve` for any checkout of the
repository exits at once, and a server whose own tree is deleted stops.
`--once` refreshes once and exits, for a cron job or a hook.

## Worktrees and other checkouts

The vectors of a repository live in one shared store beside the indexes,
named after the repository the way git names it, so every checkout of the
repository reaches the same file. A vector is keyed by the text it stands
for, so a second checkout, such as a git worktree, finds the vectors of the
first and embeds only what differs. Measured on a firmware tree of 6260
chunks, a worktree shared 100 % of its chunk hashes with the main checkout
and embedded nothing. Parsing it took 0.9 s. Chunk rows and file stamps stay
in the index of each tree, because a result names a file to open.

A search reads only the indexes of the checkout it starts in, so a worktree
kept under `.claude/worktrees/` does not join a search of the main tree, and
a search from a directory above several checkouts shows each chunk once. An
index whose tree is gone is left out of every search and said so once.
`ish --forget PATH` removes it. The vectors stay in the shared store, because
a checkout at an older commit still needs the vectors of text the main
checkout has since changed: on one firmware tree, sweeping the vectors no
index referenced took a worktree from 100 % reuse to 87 %. Sweep them only on
purpose, with `ish --sweep PATH`, which deletes the vectors no index of the
repository references.

A Markdown or AsciiDoc file with no heading is one chunk, named after the
file, when it holds a line of prose. A file of attribute definitions,
includes, and table rows holds nothing a query asks for and stays out.
`index_status` and `Ish.status()` count the files that were read and
yielded nothing, so a tree of such files is not mistaken for an index.

The index records where each chunk is — its path, line range, kind, and name —
together with the embedding vector. It does not store the source, so it is not a
second readable copy of your code. Previews are read from the file, which also
means they always show the current content.
