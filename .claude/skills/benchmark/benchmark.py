"""Measure retrieval accuracy on a fixed query set.

    uv run python .claude/skills/benchmark/benchmark.py
    uv run python .claude/skills/benchmark/benchmark.py --save before.json
    uv run python .claude/skills/benchmark/benchmark.py --baseline before.json

Report top-1 and MRR for each category and for all queries, and the
difference from a baseline when one is given. A query is a hit when a
result's path ends with the expected suffix and, when given, its symbol
holds the expected substring.
"""

import argparse
import json
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIMIT = 10


def load_queries(path: Path) -> list[dict]:
    data = tomllib.loads(path.read_text())
    queries = data.get("query", [])
    for entry in queries:
        for key in ("text", "category", "path"):
            if key not in entry:
                sys.exit(f"benchmark: a query lacks {key!r}: {entry}")
    return queries


def rank_of(results, expected_path: str, expected_symbol: str) -> int:
    """Return the 1-based rank of the first hit, or 0 when there is none."""
    for rank, match in enumerate(results, start=1):
        chunk = match.chunk
        if not str(chunk.path).endswith(expected_path):
            continue
        if expected_symbol and expected_symbol not in chunk.symbol:
            continue
        return rank
    return 0


def run(root: Path, queries: list[dict]) -> dict:
    from ish.interfaces.python.api import Ish

    ranks: dict[str, list[int]] = defaultdict(list)
    misses: list[str] = []
    with Ish(root) as session:
        for entry in queries:
            results = session.search(entry["text"], LIMIT)
            rank = rank_of(results, entry["path"], entry.get("symbol", ""))
            ranks[entry["category"]].append(rank)
            ranks["combined"].append(rank)
            if rank != 1:
                got = results[0].chunk.symbol if results else "nothing"
                where = f"rank {rank}" if rank else "absent"
                misses.append(f"  {entry['text']!r}: {where}, top was {got}")

    scores = {}
    for category, found in ranks.items():
        top1 = sum(1 for r in found if r == 1) / len(found)
        mrr = sum(1 / r for r in found if r) / len(found)
        scores[category] = {"top1": top1, "mrr": mrr, "n": len(found)}
    return {"scores": scores, "misses": misses}


def cell(score: dict, base: dict | None) -> str:
    text = f"{score['top1']:.0%} / MRR {score['mrr']:.3f}"
    if base is None:
        return text
    d_top = (score["top1"] - base["top1"]) * 100
    d_mrr = score["mrr"] - base["mrr"]
    return f"{text} ({d_top:+.0f} pts / {d_mrr:+.3f})"


def report(result: dict, baseline: dict | None) -> str:
    scores = result["scores"]
    order = [c for c in ("conceptual", "identifier") if c in scores]
    order += sorted(c for c in scores if c not in order and c != "combined")
    order.append("combined")
    lines = ["| category | n | top-1 / MRR |", "|---|---|---|"]
    for category in order:
        base = (baseline or {}).get("scores", {}).get(category)
        lines.append(
            f"| {category} | {scores[category]['n']} | {cell(scores[category], base)} |"
        )
    if result["misses"]:
        lines += ["", "Not first:", *result["misses"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--queries", type=Path, default=HERE / "queries.toml")
    parser.add_argument("--save", type=Path, help="write the scores as JSON")
    parser.add_argument("--baseline", type=Path, help="scores to compare with")
    args = parser.parse_args(argv)

    queries = load_queries(args.queries)
    try:
        result = run(args.root, queries)
    except RuntimeError as exc:
        sys.exit(f"benchmark: the backend did not answer. {exc}")

    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    print(report(result, baseline))
    if args.save:
        args.save.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nSaved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
