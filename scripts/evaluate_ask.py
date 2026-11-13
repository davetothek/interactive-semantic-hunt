"""Score `ish --ask` against a question set: citations, refusals, cost, time.

    uv run python scripts/evaluate_ask.py QUESTIONS.yaml TREE \\
        --language-model anthropic --answer-model claude-haiku-5-5 \\
        --rerank-model claude-haiku-5-5 \\
        --price-in 0.10 --price-out 0.50 --out run.json

The question set names, for each question, the file and lines that
answer it, or ``not_covered`` for a question the tree does not answer:

    - id: q01
      question: "How is the watchdog configured?"
      expected:
        - path: spec/watchdog.adoc
          lines: "120-164"
    - id: q22
      question: "What is the MTBF of the board?"
      expected: not_covered

Paths are relative to TREE. A citation hits when it names an expected
file and its lines overlap the expected range. The question set is kept
outside the repository when it names a private tree.

The reported cost is what the backend says each answer cost, rerank
included. claude-cli reports what a call would cost at the prices of the
API, also when a subscription pays for it.

The language model options are allowed for this run only, so the script
needs no change to a configuration file. The cost counts the tokens of
the rerank model as well, at the same prices, since it runs on the same
backend.

To score `ish remote ask`, give `--remote URL` and no TREE. The server
searches, and the model of this machine answers. The server names each
file from the root of its own tree, which can be above the tree of the
question set. A citation then hits when its path ends with an expected
path, at a directory boundary.
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from ish.application.answer import Answer
from ish.interfaces.python.api import Ish
from ish.interfaces.python.remote import RemoteIsh
from ish.settings import Settings, load_settings


@dataclass(frozen=True)
class Expected:
    path: str
    start: int
    end: int


@dataclass
class Scored:
    id: str
    covered_expected: bool
    covered: bool = False
    hit: bool = False
    cited: list[str] = field(default_factory=list)
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    rerank_input_tokens: int = 0
    rerank_output_tokens: int = 0
    turns: int = 0
    searches: list[str] = field(default_factory=list)
    error: str = ""
    cost_usd: float = 0.0


def load(path: Path) -> list[dict]:
    """Read the question set, and reject an item that lacks a field."""
    items = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"{path} holds no list of questions.")
    for item in items:
        for key in ("id", "question", "expected"):
            if key not in item:
                raise ValueError(f"Question {item.get('id', '?')} has no {key!r}.")
    return items


def expected_of(item: dict) -> list[Expected] | None:
    """Return the expected sources, or None for a question not covered."""
    if item["expected"] == "not_covered":
        return None
    found = []
    for entry in item["expected"]:
        start, _, end = str(entry["lines"]).partition("-")
        found.append(Expected(entry["path"], int(start), int(end or start)))
    return found


def names(cited: Path, want: str, tree: Path | None) -> bool:
    """Return True when the *cited* path of a chunk is the file *want* names.

    Under a local *tree*, compare the path from that tree. With no tree
    the chunk came from a server, which names a file from its own root.
    """
    if tree is None:
        path = cited.as_posix()
        return path == want or path.endswith(f"/{want}")
    try:
        return cited.resolve().relative_to(tree).as_posix() == want
    except ValueError:
        return False


def hits(answer: Answer, expected: list[Expected], tree: Path | None) -> bool:
    """Return True when a citation names an expected file at overlapping lines."""
    for part in answer.parts:
        for chunk in part.cites:
            for want in expected:
                if names(chunk.path, want.path, tree) and (
                    chunk.start_line <= want.end and want.start <= chunk.end_line
                ):
                    return True
    return False


def score(item: dict, ish: Ish | RemoteIsh, tree: Path | None) -> Scored:
    """Ask one question and score the answer.

    *tree* is None when *ish* reads the tree of a server.
    """
    expected = expected_of(item)
    result = Scored(id=str(item["id"]), covered_expected=expected is not None)
    started = time.perf_counter()
    try:
        answer = ish.ask(str(item["question"]))
    except Exception as exc:
        result.error = str(exc)
        result.seconds = time.perf_counter() - started
        return result
    result.seconds = time.perf_counter() - started
    result.covered = answer.covered
    result.input_tokens = answer.input_tokens
    result.output_tokens = answer.output_tokens
    result.rerank_input_tokens = answer.rerank_input_tokens
    result.rerank_output_tokens = answer.rerank_output_tokens
    result.turns = answer.turns
    result.cost_usd = answer.cost_usd
    result.searches = list(answer.searches)
    result.cited = sorted(
        {
            f"{c.path.name}:{c.start_line}-{c.end_line}"
            for p in answer.parts
            for c in p.cites
        }
    )
    if expected is not None:
        result.hit = answer.covered and hits(answer, expected, tree)
    return result


def summary(rows: list[Scored], price_in: float, price_out: float) -> dict[str, object]:
    """Reduce the scored rows to the numbers a run reports."""
    covered = [r for r in rows if r.covered_expected]
    uncovered = [r for r in rows if not r.covered_expected]
    answered = [r for r in rows if not r.error]
    cost = [
        (
            (r.input_tokens + r.rerank_input_tokens) * price_in
            + (r.output_tokens + r.rerank_output_tokens) * price_out
        )
        / 1_000_000
        for r in answered
    ]

    def share(count: int, total: int) -> str:
        return f"{count}/{total} ({100 * count / total:.0f} %)" if total else "-"

    return {
        "citation hit rate": share(sum(r.hit for r in covered), len(covered)),
        "answered when covered": share(
            sum(r.covered for r in covered if not r.error), len(covered)
        ),
        "refused when not covered": share(
            sum(not r.covered for r in uncovered if not r.error), len(uncovered)
        ),
        "errors": len(rows) - len(answered),
        "median seconds": round(statistics.median(r.seconds for r in rows), 1)
        if rows
        else 0,
        "median input tokens": statistics.median(r.input_tokens for r in answered)
        if answered
        else 0,
        "median output tokens": statistics.median(r.output_tokens for r in answered)
        if answered
        else 0,
        "median turns": statistics.median(r.turns for r in answered) if answered else 0,
        "median rerank input tokens": statistics.median(
            r.rerank_input_tokens for r in answered
        )
        if answered
        else 0,
        "cost per answer (USD)": round(statistics.mean(cost), 4) if cost else 0,
        "reported cost per answer (USD), mean": round(
            statistics.mean(r.cost_usd for r in answered), 4
        )
        if answered
        else 0,
        "reported cost per answer (USD), median": round(
            statistics.median(r.cost_usd for r in answered), 4
        )
        if answered
        else 0,
    }


def table(rows: list[Scored], numbers: dict[str, object]) -> str:
    """Render the per-question rows and the summary as Markdown."""
    lines = [
        "| id | expected | covered | hit | s | tokens in/out | cited | error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.id} | {'answer' if r.covered_expected else 'not covered'} "
            f"| {r.covered} | {r.hit if r.covered_expected else '-'} "
            f"| {r.seconds:.1f} | {r.input_tokens}/{r.output_tokens} "
            f"| {', '.join(r.cited)} | {r.error[:60]} |"
        )
    lines += ["", "| measure | value |", "|---|---|"]
    lines += [f"| {name} | {value} |" for name, value in numbers.items()]
    return "\n".join(lines)


def chosen(settings: Settings, args: argparse.Namespace) -> Settings:
    """Return *settings* with the models and the instructions of this run."""
    return replace(
        settings,
        language_model=args.language_model,
        answer_model=args.answer_model,
        rerank_model=args.rerank_model,
        ask_turns=args.ask_turns,
        prompt_dir=args.prompt_dir,
        allowed_language_models=(args.language_model,),
    )


def run(items: list[dict], ish: Ish | RemoteIsh, tree: Path | None) -> list[Scored]:
    """Ask each question in turn, and name its score on stderr."""
    rows = []
    for item in items:
        row = score(item, ish, tree)
        print(
            f"{row.id}: covered={row.covered} hit={row.hit} "
            f"{row.seconds:.1f}s {row.error[:80]}",
            file=sys.stderr,
        )
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("questions", type=Path)
    parser.add_argument("tree", type=Path, nargs="?")
    parser.add_argument(
        "--remote",
        default="",
        metavar="URL",
        help="Score `ish remote ask` against this server. Give no TREE.",
    )
    parser.add_argument("--language-model", required=True)
    parser.add_argument("--answer-model", required=True)
    parser.add_argument(
        "--rerank-model", default="", help="Model of the same backend that reranks."
    )
    parser.add_argument(
        "--ask-turns", type=int, default=0, help="Follow-up searches allowed."
    )
    parser.add_argument(
        "--prompt-dir",
        default="",
        help="Instruction files that replace the shipped ones.",
    )
    parser.add_argument("--price-in", type=float, default=0.0, help="USD per 1M.")
    parser.add_argument("--price-out", type=float, default=0.0, help="USD per 1M.")
    parser.add_argument("--only", nargs="+", default=(), help="Question ids to ask.")
    parser.add_argument("--out", type=Path, help="Write the rows as JSON here.")
    args = parser.parse_args(argv)
    if (args.tree is None) == (not args.remote):
        parser.error("Give TREE, or --remote URL, and not both.")

    items = [i for i in load(args.questions) if not args.only or i["id"] in args.only]
    if args.remote:
        settings = replace(load_settings(), remote=args.remote)
        rows = run(items, RemoteIsh(chosen(settings, args)), None)
    else:
        tree = args.tree.resolve()
        settings = chosen(load_settings(start=tree), args)
        with Ish(tree, settings=settings) as ish:
            rows = run(items, ish, tree)
    numbers = summary(rows, args.price_in, args.price_out)
    print(table(rows, numbers))
    if args.out:
        args.out.write_text(
            json.dumps(
                {"summary": numbers, "rows": [r.__dict__ for r in rows]}, indent=1
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
