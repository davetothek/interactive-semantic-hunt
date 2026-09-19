---
name: measure
description: >-
  Take the before-and-after measurement a change in this repository must
  carry: a timing, a count, or a size, on a named corpus, repeated, with the
  median reported in the form the commit body and CLAUDE.md use. Use it
  whenever a change claims to be faster, smaller, or cheaper, or when the
  user asks how much something costs.
allowed-tools: Bash(uv run *), Bash(time *), Bash(python3 *), Bash(ls *)
---

# Measure

Every performance claim in this repository is a number on a named corpus.
"Faster" is not a result. "276 ms → 48 ms on a 7,834-chunk index" is.

## 1. Write the claim first

Before running anything, write one line: *what* you expect to change, *on
which corpus*, from roughly *what* to roughly *what*. A measurement without
a prior claim finds what it wants to find.

## 2. Pick the corpus and say so

- This repository: about 33 files and 104 chunks. Small, fast, and the same
  for everyone.
- A large tree the user names. Record its file and chunk count from
  `uv run ish-mcp` `index_status`, or from `ish --scan`.

Name the corpus in the result. A number without one is not comparable.

## 3. Cold or warm, and say which

- Cold: the first run in a fresh process, and for indexing, after
  `--reindex`. It includes interpreter start and imports.
- Warm: the second run with the index open, or the second query in a
  session. It is what a picker's keystroke costs.

Measure the one the claim is about. Report both when both matter.

## 4. Repeat, and take the median

Five runs. Discard nothing. Report the median, and the spread when it is
wide.

Wall clock of a command:

```
for i in 1 2 3 4 5; do /usr/bin/time -f "%e s" uv run ish "query" src 2>&1 >/dev/null | tail -1; done
```

Where the time goes inside Python:

```
uv run python -X importtime -c "import ish.interfaces.cli.main" 2>&1 | sort -t'|' -k2 -n | tail -15
uv run python -m cProfile -s cumtime -m ish.interfaces.cli.main "query" src 2>/dev/null | head -40
```

A count rather than a time: count it from the data, not from a log. Rows in
the index, chunks a parser emitted, lines a run printed.

## 5. Report in the house form

```
Measured on <corpus>: <what> <before> → <after> (<cold|warm>, median of 5).
```

With the thing that changed and why the number moved, in one more sentence.
Put it in the commit body. When the number justifies a rule, add the line to
`.claude/CLAUDE.md` under **The evidence**, in the section it belongs to.

## What is not a measurement

- A number from one run.
- A number with no corpus.
- "About the same" without the two numbers that are about the same.
- A profile without the wall clock it explains.
