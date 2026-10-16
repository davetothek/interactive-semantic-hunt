---
paths:
  - "src/ish/adapters/vector_store/**"
  - "src/ish/application/index.py"
  - "src/ish/application/scan.py"
  - "src/ish/adapters/vcs/**"
---

# Indexing

## What enters the index

- Every rule about what to index lives in `Scan.accepts()` and nowhere else.
  Discovery and pruning both ask that one predicate.
- `include` and `exclude` are regular expressions over the POSIX path.
  `exclude` is searched against the path from the top of the checkout,
  written with a leading slash, and against the whole path outside a
  repository. `include` is matched from the start of the path written from
  the tree root, so it names a place and not a segment. `exclude` beats
  `include`. A malformed pattern names its option and stops the run.
- `ignore` names and `exclude` patterns judge only what lies inside the
  checkout, never a directory above it. A worktree under
  `.claude/worktrees/` must be indexable with the configuration that keeps
  `.claude` out of the main index. The checkout reaches the scan as
  `anchor`, from `bootstrap`, which asks git once per search.
- The walk tests `exclude` against each directory it meets, written with a
  trailing slash, and never enters one that matches. `ignore` prunes by
  name, `exclude` by path. Both prune.
- Ask git what it ignores through the `vcs` adapter. Do not reimplement
  ignore rules. Outside a repository, or without git, ignore nothing.
  `unignore` lets a path git ignores in anyway, anchored like `include`.
  `exclude` still beats it.
- A query-scope filter (`lang`, `under`, `type`) never reaches
  `Scan.accepts()`. It would make the next run prune what it excluded.

## What leaves the index

- Prune on a positive test only. A file leaves when it is gone from disk, or
  when `Scan.accepts()` rejects it. Absence from a walk is not evidence.
- A permission error means "cannot tell" and keeps the entry. Only
  `FileNotFoundError` means gone.
- Prune orphans within the scanned tree only.
- `remove_files` and `clear` keep vectors. `prune_vectors` sweeps them on
  demand.

## Staleness and stamps

- Compare `(mtime_ns, size)` first. Read and hash only what differs. Never
  hash every file on every query.
- A file that yields no chunks is stamped. A file nobody could read is not.
  `parse_file()` returns an empty sequence for the first and `None` for the
  second.
- Vectors are keyed by `(content_hash, model_id)`, not by path.
- The store records the chunking stamp its files were read under.
  `bootstrap.chunking_stamp()` composes it from `CHUNKING_VERSION` and the
  caps. A refresh that finds another stamp clears the files, keeps the
  vectors, and reads every file again. Raise `CHUNKING_VERSION` when a
  parser changes where a file divides.

## The SQLite store

- Opening an index writes nothing to it. Read the stored root first and write
  only a different one.
- Persist vectors every 64 chunks, and write the rows of every file whose
  vectors are all stored right after. A reader sees the run as it goes.
- A schema change bumps `SCHEMA_VERSION` and vacuums. Dropping a table
  without a vacuum leaves its pages readable on disk.
- The index stores where a chunk is, never what it says: vectors, paths,
  line ranges, kinds, symbols. A preview reads the file.
- Open with `check_same_thread=False` and guard every statement with one
  lock. The TUI reaches the store from worker threads. `TestThreadSafety`
  exists for this.
- The scored matrix cache is dropped when this store writes, and when
  `PRAGMA data_version` shows another connection committed.
- Every write transaction goes through `_mark_written()`, which bumps the
  `generation` row in `meta`. The matrix file beside the index is stamped
  with that generation and the chunk count, and a file whose stamp differs
  is read past and written again.
- The matrix file is a cache. SQLite stays the source of truth, and the
  file holds ids and vectors only. Write it under a temporary name and
  rename it into place, and never on a miss within `SETTLE_SECONDS` of a
  write by this store.
- Every interface calls `Search.close()`.

## Federation

- A search of a parent reads the indexes below it and writes to none. It
  warns that it refreshed nothing. `refresh_indexes()` visits each tree.
- Asking inside an indexed tree reads that tree's index and narrows the
  answers to the path asked for.
- A search reads the indexes of its own checkout. `indexes_for()` applies
  the rule for the search, the refresh, and the status alike, and leaves
  out an index whose tree is gone.

## Repositories and checkouts

- An index records `root`, `repository`, and `checkout` in `meta`. The
  composition root asks git through the vcs adapter and passes paths. The
  application never learns what git is.
- The vectors of a repository live in one shared store, named by the
  catalog after the common directory. An index names the store it uses in
  `meta`, so it reaches it without git. Chunk rows and file stamps stay
  per tree.
- `prune_vectors()` on a shared store asks every sibling index before it
  deletes, siblings whose tree is gone included. A sibling that cannot be
  read keeps everything.
