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

``--repeat N`` asks the whole set N times, one round after the other,
and reports the mean and the spread of each measure over the rounds. A
model gives a different answer from run to run, so a delta between two
runs is real only when it is larger than that spread.

A model usage limit stops the run. The run is then incomplete, and the
question that met the limit, and each question after it, count as no
miss and no hit.

Each run appends one line to ``$XDG_DATA_HOME/ish/eval/history.jsonl``:
the time, the ish revision, the options, the hash of the question set,
and the scores. ``--label NAME`` names the line. ``--compare A B``
prints the deltas from line A to line B, accuracy first, then tokens and
cost, then time. A line is a label, a 1-based line number, or a negative
number that counts from the end:

    uv run python scripts/evaluate_ask.py --compare baseline -1

Every setting of ish can change for one run, with no change to the
code. ``--config FILE`` reads one config file in place of the files of
the tree, and ``--set KEY=VALUE`` sets one option, such as
``--set challenge=false`` or ``--set prompt_cache=off``. With either,
the run takes its settings from the files, the environment, and
``--set``, and only the model flags given override them. The history
line keeps every answer setting of the run, and ``--compare`` names the
settings that differ.

``--trace DIR`` keeps the input and the output of every model call, in
one directory for each question. ``scripts/replay_call.py`` runs one of
those calls again alone.
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ish.application.answer import Answer
from ish.interfaces.python.api import Ish
from ish.interfaces.python.remote import RemoteIsh
from ish.settings import ANSWERS, ConfigError, Settings, load_settings

# The words of an error that says the model usage of the account is spent.
# claude-cli writes "You've hit your 5-hour limit" or "weekly limit".
# A question asked after it fails the same way until the limit resets.
USAGE_LIMIT = re.compile(r"\bhit your [^.]{0,40}?limit\b|\busage limit\b", re.I)

REPOSITORY = Path(__file__).resolve().parents[1]


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
    check_input_tokens: int = 0
    check_output_tokens: int = 0
    claims_checked: int = 0
    unconfirmed: int = 0
    reads: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    round: int = 1
    limited: bool = False


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
        result.limited = USAGE_LIMIT.search(result.error) is not None
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
    result.check_input_tokens = answer.check_input_tokens
    result.check_output_tokens = answer.check_output_tokens
    result.claims_checked = answer.claims_checked
    result.unconfirmed = len(answer.unconfirmed)
    result.reads = answer.reads
    result.cache_write_tokens = answer.cache_write_tokens
    result.cache_read_tokens = answer.cache_read_tokens
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
    """Render the per-question rows and the summary as Markdown.

    Mark each id with its round when the rows hold more than one round.
    """
    rounds = len({r.round for r in rows}) > 1
    lines = [
        "| id | expected | covered | hit | s | tokens in/out | cited | error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {f'{r.id} #{r.round}' if rounds else r.id} "
            f"| {'answer' if r.covered_expected else 'not covered'} "
            f"| {r.covered} | {r.hit if r.covered_expected else '-'} "
            f"| {r.seconds:.1f} | {r.input_tokens}/{r.output_tokens} "
            f"| {', '.join(r.cited)} | {r.error[:60]} |"
        )
    lines += ["", "| measure | value |", "|---|---|"]
    lines += [f"| {name} | {value} |" for name, value in numbers.items()]
    return "\n".join(lines)


# The flags of the run that set an option, and the value each one sets
# when it is not given and no --config or --set describes the run.
FLAGS = {
    "language_model": "",
    "answer_model": "",
    "rerank_model": "",
    "ask_turns": 0,
    "prompt_dir": "",
}


def chosen(settings: Settings, args: argparse.Namespace) -> Settings:
    """Return *settings* with the models and the instructions of this run.

    A flag that is given always wins, except over ``--set`` of the same
    option. With no ``--config`` and no ``--set``, a flag that is not
    given sets its value from ``FLAGS``. Allow the language model that
    the run uses, and no other.
    """
    described = bool(args.config or args.set)
    sets = dict(parse_sets(args.set))
    given = {}
    for name, default in FLAGS.items():
        value = getattr(args, name)
        if name in sets:
            continue
        if value is not None:
            given[name] = value
        elif not described:
            given[name] = default
    picked = replace(settings, **given)
    return replace(picked, allowed_language_models=(picked.language_model,))


def parse_sets(words: list[str]) -> list[tuple[str, str]]:
    """Return each ``KEY=VALUE`` of ``--set``, and refuse an unknown option."""
    known = {f.name for f in fields(Settings)}
    found = []
    for word in words:
        key, equals, value = word.partition("=")
        key = key.strip().replace("-", "_")
        if not equals or not key:
            raise ValueError(f"--set takes KEY=VALUE, not {word!r}.")
        if key not in known:
            raise ValueError(f"--set names no option of ish: {key!r}.")
        found.append((key, value))
    return found


def answer_settings(settings: Settings) -> dict[str, object]:
    """Return every answer setting of a run, as JSON can hold it."""
    return {
        f.name: list(value) if isinstance(value, tuple) else value
        for f in fields(Settings)
        if f.metadata.get("section") == ANSWERS
        for value in [getattr(settings, f.name)]
    }


def filed(trace: Path, row: "Scored", repeat: int) -> None:
    """Move the calls of the question of *row* into a directory of its own."""
    name = f"{row.id}-{row.round}" if repeat > 1 else row.id
    target = trace / name
    target.mkdir(parents=True, exist_ok=True)
    for call in sorted(trace.glob("*.json")):
        call.rename(target / call.name)


def run(
    items: list[dict],
    ish: Ish | RemoteIsh,
    tree: Path | None,
    repeat: int = 1,
    trace: Path | None = None,
) -> tuple[list[Scored], str]:
    """Ask each question *repeat* times, a round at a time, and name each score.

    Stop at the first error that names a model usage limit, and return
    that error with the rows before it. Return an empty error when the
    run asked every question. With a *trace*, file the calls of each
    question in a directory of its own.
    """
    rows = []
    for number in range(1, repeat + 1):
        for item in items:
            row = score(item, ish, tree)
            row.round = number
            if trace is not None:
                filed(trace, row, repeat)
            mark = f" #{number}" if repeat > 1 else ""
            print(
                f"{row.id}{mark}: covered={row.covered} hit={row.hit} "
                f"{row.seconds:.1f}s {row.error[:80]}",
                file=sys.stderr,
            )
            if row.limited:
                return rows, f"{row.id}{mark}: {row.error}"
            rows.append(row)
    return rows, ""


@dataclass(frozen=True)
class Measure:
    """Name one number of a run, and the direction in which it is better."""

    key: str
    label: str
    higher_is_better: bool
    percent: bool = False


# The measures of a history line, in the order a comparison reads them:
# accuracy first, then tokens and cost, then time.
MEASURES = (
    Measure("hit_rate", "citation hit rate", True, percent=True),
    Measure("answered_rate", "answered when covered", True, percent=True),
    Measure("refused_rate", "refused when not covered", True, percent=True),
    Measure("unconfirmed", "unconfirmed claims per answer, mean", False),
    Measure("errors", "errors", False),
    Measure("input_tokens", "median input tokens", False),
    Measure("cache_write_tokens", "median input tokens written to the cache", False),
    Measure("cache_read_tokens", "median input tokens read from the cache", False),
    Measure("output_tokens", "median output tokens", False),
    Measure("rerank_input_tokens", "median rerank input tokens", False),
    Measure("check_input_tokens", "median check input tokens", False),
    Measure("all_input_tokens", "median input tokens, all models", False),
    Measure("turns", "median turns", False),
    Measure("reads", "median rounds of reading", False),
    Measure("cost_usd", "reported cost per answer (USD), mean", False),
    Measure("seconds", "median seconds", False),
)


def measures(rows: list[Scored]) -> dict[str, float]:
    """Reduce the rows of one round to the numbers of a history line.

    Leave out a rate that has no question to count, and every number of
    an answer when no question was answered.
    """
    covered = [r for r in rows if r.covered_expected]
    uncovered = [r for r in rows if not r.covered_expected]
    answered = [r for r in rows if not r.error]
    found: dict[str, float] = {"errors": len(rows) - len(answered)}
    if covered:
        found["hit_rate"] = sum(r.hit for r in covered) / len(covered)
        found["answered_rate"] = sum(r.covered and not r.error for r in covered) / len(
            covered
        )
    if uncovered:
        found["refused_rate"] = sum(
            not r.covered and not r.error for r in uncovered
        ) / len(uncovered)
    if rows:
        found["seconds"] = statistics.median(r.seconds for r in rows)
    if not answered:
        return found
    with_claims = [r for r in answered if r.covered]
    if with_claims:
        found["unconfirmed"] = statistics.mean(r.unconfirmed for r in with_claims)
    found["input_tokens"] = statistics.median(r.input_tokens for r in answered)
    found["cache_write_tokens"] = statistics.median(
        r.cache_write_tokens for r in answered
    )
    found["cache_read_tokens"] = statistics.median(
        r.cache_read_tokens for r in answered
    )
    found["output_tokens"] = statistics.median(r.output_tokens for r in answered)
    found["rerank_input_tokens"] = statistics.median(
        r.rerank_input_tokens for r in answered
    )
    found["check_input_tokens"] = statistics.median(
        r.check_input_tokens for r in answered
    )
    found["all_input_tokens"] = statistics.median(
        r.input_tokens + r.rerank_input_tokens + r.check_input_tokens for r in answered
    )
    found["turns"] = statistics.median(r.turns for r in answered)
    found["reads"] = statistics.median(r.reads for r in answered)
    found["cost_usd"] = statistics.mean(r.cost_usd for r in answered)
    return found


def rounds_of(rows: list[Scored], complete: bool) -> list[list[Scored]]:
    """Group *rows* by round, and keep only the rounds that asked every question.

    A stopped run leaves its last round short. Keep that round only when
    it is the one round there is, or when the run is *complete*.
    """
    grouped: dict[int, list[Scored]] = {}
    for row in rows:
        grouped.setdefault(row.round, []).append(row)
    every = list(grouped.values())
    if complete or len(every) < 2:
        return every
    return every[:-1]


def spread(rounds: list[list[Scored]]) -> tuple[dict[str, float], dict[str, list]]:
    """Return the mean of each measure over *rounds*, and its lowest and highest."""
    each = [measures(r) for r in rounds]
    mean: dict[str, float] = {}
    span: dict[str, list] = {}
    for m in MEASURES:
        values = [found[m.key] for found in each if m.key in found]
        if values:
            mean[m.key] = statistics.mean(values)
            span[m.key] = [min(values), max(values)]
    return mean, span


def shown(measure: Measure, value: float | None) -> str:
    """Render one value of *measure*, or a dash for one that is not there."""
    if value is None:
        return "-"
    if measure.percent:
        return f"{100 * value:.0f} %"
    return f"{value:.4f}" if measure.key == "cost_usd" else f"{value:g}"


def spread_table(mean: dict[str, float], span: dict[str, list], count: int) -> str:
    """Render the mean and the range of each measure over *count* rounds."""
    lines = [
        f"Over {count} rounds:",
        "",
        "| measure | mean | lowest | highest |",
        "|---|---|---|---|",
    ]
    for m in MEASURES:
        if m.key in mean:
            low, high = span[m.key]
            lines.append(
                f"| {m.label} | {shown(m, mean[m.key])} "
                f"| {shown(m, low)} | {shown(m, high)} |"
            )
    return "\n".join(lines)


def history_file() -> Path:
    """Return the file that keeps one line for each run."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ish" / "eval" / "history.jsonl"


def revision(repository: Path = REPOSITORY) -> dict[str, object]:
    """Return the commit of *repository*, and whether a tracked file differs from it."""

    def git(*words: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(repository), *words],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    sha = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no")) if sha else False
    return {"sha": sha, "dirty": dirty}


def history_line(
    args: argparse.Namespace,
    items: list[dict],
    rows: list[Scored],
    stopped: str,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    settings: Settings | None = None,
) -> dict[str, object]:
    """Return the history line of one run."""
    used = rounds_of(rows, complete=not stopped)
    mean, span = spread(used)
    options = {
        key: (str(value) if isinstance(value, Path) else value)
        for key, value in vars(args).items()
        if key not in ("compare", "label", "out")
    }
    return {
        "time": now().isoformat(timespec="seconds"),
        "label": args.label,
        "ish": revision(),
        "options": options,
        "questions": {
            "sha256": hashlib.sha256(args.questions.read_bytes()).hexdigest(),
            "ids": [str(i["id"]) for i in items],
        },
        "repeat": args.repeat,
        "rounds": len(used),
        "complete": not stopped,
        "stopped": stopped,
        "scores": mean,
        "spread": span,
        "settings": answer_settings(settings) if settings else {},
    }


def append(line: dict[str, object], path: Path) -> None:
    """Append *line* to the history at *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as history:
        history.write(json.dumps(line) + "\n")


def pick(lines: list[dict], name: str) -> tuple[int, dict]:
    """Return the number and the history line that *name* selects.

    *name* is a 1-based line number, a negative number that counts from
    the end, or a label, which selects the last line that has it.
    """
    if re.fullmatch(r"-?\d+", name):
        number = int(name)
        index = number - 1 if number > 0 else len(lines) + number
        if number != 0 and 0 <= index < len(lines):
            return index + 1, lines[index]
        raise ValueError(f"The history has no line {name}. It has {len(lines)}.")
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].get("label") == name:
            return index + 1, lines[index]
    raise ValueError(f"No line of the history has the label {name!r}.")


def described(number: int, line: dict) -> str:
    """Name a history line in one line of text."""
    ish = line.get("ish") or {}
    sha = str(ish.get("sha", ""))[:7] + ("+dirty" if ish.get("dirty") else "")
    options = line.get("options") or {}
    models = "/".join(
        str(options.get(k) or "-")
        for k in ("answer_model", "rerank_model", "ask_turns")
    )
    state = "complete" if line.get("complete") else f"stopped: {line.get('stopped')}"
    label = f" {line['label']!r}" if line.get("label") else ""
    return (
        f"line {number}{label}, {line.get('time')}, ish {sha}, "
        f"answer/rerank/turns {models}, {line.get('rounds')} round(s), {state}"
    )


def compare(a: tuple[int, dict], b: tuple[int, dict]) -> str:
    """Render the deltas from history line *a* to line *b* as Markdown.

    A delta is real when it is larger than the wider of the two ranges
    over rounds. A run of one round has no range, and a delta against it
    cannot be judged.
    """
    (na, la), (nb, lb) = a, b
    lines = [f"A: {described(na, la)}", f"B: {described(nb, lb)}", ""]
    qa, qb = la.get("questions") or {}, lb.get("questions") or {}
    if (qa.get("sha256"), qa.get("ids")) != (qb.get("sha256"), qb.get("ids")):
        lines += ["The two runs asked different questions.", ""]
    for line, name in ((la, "A"), (lb, "B")):
        if not line.get("complete"):
            lines += [f"Run {name} is incomplete.", ""]
    changed = differences(la.get("settings") or {}, lb.get("settings") or {})
    if changed:
        lines += ["Settings that differ:", "", *changed, ""]
    lines += [
        "| measure | A | B | B - A | spread | verdict |",
        "|---|---|---|---|---|---|",
    ]
    sa, sb = la.get("scores") or {}, lb.get("scores") or {}
    pa, pb = la.get("spread") or {}, lb.get("spread") or {}
    for m in MEASURES:
        if m.key not in sa and m.key not in sb:
            continue
        va, vb = sa.get(m.key), sb.get(m.key)
        widths = [
            high - low
            for line, ranges in ((la, pa), (lb, pb))
            if (line.get("rounds") or 0) > 1 and m.key in ranges
            for low, high in [ranges[m.key]]
        ]
        width = max(widths) if widths else None
        if va is None or vb is None:
            delta, verdict = None, "-"
        else:
            delta = vb - va
            verdict = judged(m, delta, width)
        lines.append(
            f"| {m.label} | {shown(m, va)} | {shown(m, vb)} "
            f"| {signed(m, delta)} | {shown(m, width)} | {verdict} |"
        )
    return "\n".join(lines)


def differences(a: dict, b: dict) -> list[str]:
    """Return one line for each setting whose value differs from run A to run B."""
    return [
        f"- {name}: {a.get(name, '-')!r} -> {b.get(name, '-')!r}"
        for name in sorted({*a, *b})
        if a.get(name) != b.get(name)
    ]


def signed(measure: Measure, delta: float | None) -> str:
    """Render a delta with its sign."""
    if delta is None:
        return "-"
    text = shown(measure, abs(delta))
    return f"+{text}" if delta > 0 else f"-{text}" if delta < 0 else text


def judged(measure: Measure, delta: float, width: float | None) -> str:
    """Say whether *delta* is better or worse, and whether it is real."""
    if delta == 0:
        return "same"
    better = (delta > 0) == measure.higher_is_better
    word = "better" if better else "worse"
    if width is None:
        return f"{word}, no spread"
    return word if abs(delta) > width else f"{word}, within spread"


def compared(names: list[str], path: Path) -> str:
    """Return the comparison of the two history lines that *names* selects."""
    if not path.is_file():
        raise ValueError(f"{path} holds no history yet.")
    lines = [
        json.loads(text)
        for text in path.read_text(encoding="utf-8").splitlines()
        if text.strip()
    ]
    return compare(pick(lines, names[0]), pick(lines, names[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("questions", type=Path, nargs="?")
    parser.add_argument("tree", type=Path, nargs="?")
    parser.add_argument(
        "--remote",
        default="",
        metavar="URL",
        help="Score `ish remote ask` against this server. Give no TREE.",
    )
    parser.add_argument("--language-model")
    parser.add_argument("--answer-model")
    parser.add_argument(
        "--rerank-model", help="Model of the same backend that reranks."
    )
    parser.add_argument("--ask-turns", type=int, help="Follow-up searches allowed.")
    parser.add_argument(
        "--prompt-dir",
        help="Instruction files that replace the shipped ones.",
    )
    parser.add_argument(
        "--config", type=Path, help="Read this config file in place of the tree's."
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set one option of ish for this run. Give it once for each option.",
    )
    parser.add_argument(
        "--trace", type=Path, help="Keep every model call, by question, here."
    )
    parser.add_argument("--price-in", type=float, default=0.0, help="USD per 1M.")
    parser.add_argument("--price-out", type=float, default=0.0, help="USD per 1M.")
    parser.add_argument("--only", nargs="+", default=(), help="Question ids to ask.")
    parser.add_argument("--out", type=Path, help="Write the rows as JSON here.")
    parser.add_argument(
        "--repeat", type=int, default=1, help="Ask the whole set this many times."
    )
    parser.add_argument("--label", default="", help="Name the history line.")
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("A", "B"),
        help="Print the deltas from history line A to line B, and ask nothing.",
    )
    args = parser.parse_args(argv)
    if args.compare:
        try:
            print(compared(args.compare, history_file()))
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    if args.questions is None:
        parser.error("Give QUESTIONS, or --compare A B.")
    if args.repeat < 1:
        parser.error("--repeat takes a number of 1 or more.")
    if (args.tree is None) == (not args.remote):
        parser.error("Give TREE, or --remote URL, and not both.")

    try:
        overrides: dict[str, object] = dict(parse_sets(args.set))
        if args.config:
            overrides["config"] = str(args.config)
        if args.trace:
            overrides["ask_trace"] = str(args.trace)
        start = None if args.remote else args.tree.resolve()
        settings = chosen(load_settings(overrides, start=start), args)
    except (ValueError, ConfigError) as exc:
        parser.error(str(exc))
    if not (settings.language_model and settings.answer_model):
        parser.error("Give --language-model and --answer-model, or set them.")

    items = [i for i in load(args.questions) if not args.only or i["id"] in args.only]
    if args.remote:
        settings = replace(settings, remote=args.remote)
        rows, stopped = run(items, RemoteIsh(settings), None, args.repeat, args.trace)
    else:
        assert start is not None
        with Ish(start, settings=settings) as ish:
            rows, stopped = run(items, ish, start, args.repeat, args.trace)
    numbers = summary(rows, args.price_in, args.price_out)
    report = table(rows, numbers)
    line = history_line(args, items, rows, stopped, settings=settings)
    if args.repeat > 1 and line["rounds"]:
        mean, span = line["scores"], line["spread"]
        assert isinstance(mean, dict) and isinstance(span, dict)
        report += "\n\n" + spread_table(mean, span, int(str(line["rounds"])))
    if stopped:
        report = (
            f"Incomplete: a model usage limit stopped the run at {stopped}\n"
            f"{len(rows)} of {len(items) * args.repeat} answers are scored. "
            "The answers after the limit count as no miss.\n\n" + report
        )
    print(report)
    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "summary": numbers,
                    "complete": not stopped,
                    "stopped": stopped,
                    "rows": [r.__dict__ for r in rows],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
    append(line, history_file())
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
