---
name: benchmark
description: >-
  Measure retrieval accuracy on a fixed query set: top-1 and MRR for
  conceptual, identifier, and combined queries, against a saved baseline.
  Run it before and after any change to ranking, task prefixes, the
  is_code_like gate, or chunking, and paste the table into the commit body.
allowed-tools: Bash(uv run *), Bash(ls *), Bash(cat *)
---

# Benchmark

Accuracy beats speed. A change to `ranking.py`, `prefixes.py`, `_limits.py`,
or a parser's chunking is not done until this table says what it cost.

## Requirements

An embedding backend must be reachable: Ollama with `nomic-embed-text`, or
whatever `.ish/config.toml` selects. The script fails with the backend's own
message when it is not. Never fill in a number by hand.

The first run over a tree builds its index. On this repository that costs
about 90 seconds. Later runs reuse it.

## Procedure

1. Before the change, on the branch point:

   ```
   uv run python .claude/skills/benchmark/benchmark.py --save /tmp/before.json
   ```

2. Make the change.

3. After:

   ```
   uv run python .claude/skills/benchmark/benchmark.py --baseline /tmp/before.json
   ```

The script prints the table and, with a baseline, the difference per cell.
Paste the table into the commit body, and into `CLAUDE.md` under **The
evidence** when the result justifies a rule.

## Reading the result

- Top-1 is the share of queries whose first result is the expected chunk.
- MRR is the mean of `1 / rank` of the first expected result within the top
  ten, with 0 when it is absent.
- A drop of one query in twenty is 5 points of top-1. Say which query moved,
  not only the total. The script lists misses.
- The shipped gate keeps conceptual queries on the vector order alone. If a
  change moves conceptual top-1 down, the change is wrong for this
  repository whatever it does for identifiers.

## The query set

`queries.toml` beside this file holds the queries for this repository. Each
entry names the query, its category, and the chunk that answers it by path
suffix and, when it matters, by a substring of the symbol.

Add a query when a defect in ranking is found: the query that exposed it, and
the chunk it should have found. Do not remove a query because it fails.

To benchmark another tree, pass `--root <path> --queries <file>`.
