---
name: chunk-diff
description: >-
  Prove what a change did to chunking: snapshot every chunk the parsers
  make over a set of trees before and after, and compare. Use it for every
  change under src/ish/adapters/parser/, for a move that claims no chunk
  changed, and for a fix that claims which chunks it changed. Paste the
  result into the commit body.
allowed-tools: Bash(uv run *), Bash(git worktree *), Bash(ls *)
---

# Chunk diff

A parser change says one of two things: no chunk changed, or these chunks
changed. Either is a claim until two snapshots agree, or differ where the
change says. `/benchmark` says what a change cost in accuracy. This says
what it did to the chunks.

## Choose the trees

Use trees that hold every language the change can reach. This repository
holds Python, Markdown, YAML, JSON, and TOML. A change to AsciiDoc or C and
C++ needs a tree that holds them, with includes for AsciiDoc. Name the
trees in the commit body by what they hold, not by where they are, when
they are private.

## Procedure

1. Before the change, or from the base of a branch that already holds it:

   ```
   git worktree add --detach /tmp/chunk-base origin/main
   uv run python scripts/chunk_snapshot.py --source /tmp/chunk-base \
       --out /tmp/before.jsonl TREE...
   ```

   Without `--source`, the snapshot reads the code of this checkout. Take it
   before the change in that case.

2. After the change:

   ```
   uv run python scripts/chunk_snapshot.py --out /tmp/after.jsonl TREE...
   ```

3. Compare:

   ```
   uv run python scripts/chunk_snapshot.py --compare /tmp/before.jsonl /tmp/after.jsonl
   git worktree remove --force /tmp/chunk-base
   ```

The comparison prints the files, chunks, errors, and empty files of each
side, then
`identical`, or each record that is on one side only, and exits 1.

## Reading the result

- A move or a rename: `identical`, or the change is not a move.
- A fix: every record that differs is one the fix names. A difference the
  change does not explain is a defect. Find it before the commit.
- A changed chunk raises `CHUNKING_VERSION` in `parser/__init__.py`, and
  `/benchmark` runs before and after.
- Put the two summary lines and the verdict in the commit body. Do not paste
  chunk records of a private tree: they hold its paths and headings.
