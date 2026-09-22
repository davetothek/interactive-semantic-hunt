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
  `exclude` is searched against the whole path. `include` is matched from
  the start of the path written from the tree root, so it names a place and
  not a segment. `exclude` beats `include`. A malformed pattern names its
  option and stops the run.
- The walk tests `exclude` against each directory it meets, written with a
  trailing slash, and never enters one that matches. `ignore` prunes by
  name, `exclude` by path. Both prune.
- Ask git what it ignores through the `vcs` adapter. Do not reimplement
  ignore rules. Outside a repository, or without git, ignore nothing.
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

## The SQLite store

- Opening an index writes nothing to it. Read the stored root first and write
  only a different one.
- Persist vectors every 64 chunks. Chunk rows land at the end of a file.
- A schema change bumps `SCHEMA_VERSION` and vacuums. Dropping a table
  without a vacuum leaves its pages readable on disk.
- The index stores where a chunk is, never what it says: vectors, paths,
  line ranges, kinds, symbols. A preview reads the file.
- Open with `check_same_thread=False` and guard every statement with one
  lock. The TUI reaches the store from worker threads. `TestThreadSafety`
  exists for this.
- The scored matrix cache is dropped when this store writes, and when
  `PRAGMA data_version` shows another connection committed.
- Every interface calls `Search.close()`.

## Federation

- A search of a parent reads the indexes below it and writes to none. It
  warns that it refreshed nothing. `refresh_indexes()` visits each tree.
- Asking inside an indexed tree reads that tree's index and narrows the
  answers to the path asked for.
