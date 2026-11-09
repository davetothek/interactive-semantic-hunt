"""Write every chunk the parsers make for a set of trees, or compare two such files.

A change to a parser claims either that no chunk changed or that some
did. This script turns the claim into a fact. Take a snapshot before the
change and one after, over the same trees, and compare them:

    uv run python scripts/chunk_snapshot.py --out before.jsonl TREE...
    uv run python scripts/chunk_snapshot.py --out after.jsonl TREE...
    uv run python scripts/chunk_snapshot.py --compare before.jsonl after.jsonl

``--source DIR`` reads the ``ish`` package from ``DIR/src``, so a
snapshot of the code of another checkout runs from this one.

A line holds one chunk: the tree, the path in it, the language, kind,
symbol, lines, and a hash of the text. A file that raises is a line with
the error. The parsers run as an index run builds them, with every cap,
and without the parsers of the user, so two machines agree.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from collections.abc import Iterator
from pathlib import Path

# Directories that hold no source a tree means to index.
SKIP = {".git", ".cache", "node_modules", "site", "__pycache__", "worktrees"}

# Lines of difference to print before the count of the rest.
SHOWN = 20


def snapshot(trees: list[Path]) -> Iterator[dict]:
    """Yield one record for each chunk, or each error, in *trees*, in a fixed order."""
    from ish.bootstrap import build_parsers
    from ish.settings import Settings

    parsers = build_parsers(Settings(plugins=False))
    by_suffix = {suffix: parser for parser in parsers for suffix in parser.suffixes}
    for tree in trees:
        for directory, names, files in os.walk(tree):
            names[:] = sorted(
                n for n in names if n not in SKIP and not n.startswith(".venv")
            )
            for name in sorted(files):
                path = Path(directory, name)
                parser = by_suffix.get(path.suffix.lower())
                if parser is None:
                    continue
                where = {"tree": tree.name, "path": path.relative_to(tree).as_posix()}
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                    chunks = parser.parse(path, source)
                except Exception as exc:
                    yield {**where, "error": type(exc).__name__}
                    continue
                for chunk in chunks:
                    yield {
                        **where,
                        "language": chunk.language,
                        "kind": chunk.kind,
                        "symbol": chunk.symbol,
                        "lines": [chunk.start_line, chunk.end_line],
                        "text": hashlib.sha256(chunk.text.encode()).hexdigest()[:16],
                    }


def compare(before: list[dict], after: list[dict]) -> list[str]:
    """Return a line for each record in one snapshot and not in the other."""
    old = {json.dumps(record, sort_keys=True) for record in before}
    new = {json.dumps(record, sort_keys=True) for record in after}
    return [f"- {line}" for line in sorted(old - new)] + [
        f"+ {line}" for line in sorted(new - old)
    ]


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _summary(records: list[dict]) -> str:
    files = {(r["tree"], r["path"]) for r in records}
    errors = sum("error" in r for r in records)
    return f"{len(files)} files, {len(records) - errors} chunks, {errors} errors"


def main(argv: list[str] | None = None) -> int:
    """Run the command line. Return 1 when two snapshots differ."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trees", nargs="*", type=Path, help="trees to parse")
    parser.add_argument("--out", type=Path, help="write the snapshot here")
    parser.add_argument("--source", type=Path, help="read ish from DIR/src")
    parser.add_argument(
        "--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"), help="compare"
    )
    args = parser.parse_args(argv)

    if args.compare:
        before, after = (_read(path) for path in args.compare)
        print(f"before: {_summary(before)}")
        print(f"after:  {_summary(after)}")
        lines = compare(before, after)
        if not lines:
            print("identical")
            return 0
        print(f"{len(lines)} records differ:")
        for line in lines[:SHOWN]:
            print(line)
        if len(lines) > SHOWN:
            print(f"... and {len(lines) - SHOWN} more")
        return 1

    if not args.out or not args.trees:
        parser.error("give --out and at least one tree, or --compare")
    if args.source:
        sys.path.insert(0, str(args.source.resolve() / "src"))
    logging.disable(logging.CRITICAL)
    records = list(snapshot([tree.resolve() for tree in args.trees]))
    args.out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))
    print(_summary(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
