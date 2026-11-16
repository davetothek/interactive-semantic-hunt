"""Run one call of claude-cli again, alone, from the file its trace wrote.

    uv run python scripts/replay_call.py TRACE/q07/004-step.json \\
        --prompt-cache off --repeat 3

An answer makes several calls: a rerank, the turns of the answer, and
the checks. ``ask_trace``, or ``--trace`` of ``evaluate_ask.py``, writes
one JSON file for each call: the instructions, the message, the schema,
the reply, the tokens, and the cost. This script sends that call again
with the same arguments that ish builds, and prints what it read, wrote,
and cost each time. Change one part of the call to see what it does:

- ``--model`` asks another model.
- ``--prompt-cache`` sets the cache: 5m, 1h, or off.
- ``--read-usd`` sets what a call that reads the tree may cost.
- ``--effort`` sets the effort: low, medium, high, xhigh, or max.
- ``--system FILE`` and ``--prompt FILE`` replace the instructions or the
  message.
- ``--no-tools`` runs a call that read the tree in an empty directory,
  with no tool. Its instructions still hold the rules for the tools.

``--trace DIR`` writes each new call there, in the same form, so two
replies can be compared side by side. A call costs what claude reports,
from the quota of the account that claude is signed in to.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from ish.adapters.language_model import claude_cli
from ish.adapters.language_model._calls import (
    DEFAULT_READ_USD,
    EFFORTS,
    PROMPT_CACHES,
    write_trace,
)
from ish.application.ports.language_model import LanguageModelError

FIELDS = ("kind", "model", "tools", "tree", "system", "schema", "prompt")


def load(path: Path) -> dict:
    """Read one call of a trace, and refuse a file that lacks a field."""
    record = json.loads(path.read_text(encoding="utf-8"))
    missing = [name for name in FIELDS if name not in record]
    if missing:
        raise ValueError(f"{path} is no call of a trace: no {', '.join(missing)}.")
    return record


def replay(
    record: dict,
    *,
    model: str,
    prompt_cache: str,
    read_usd: float,
    tools: bool,
    timeout: float,
    effort: str = "",
) -> dict:
    """Send the call of *record* once, and return it as a record of a trace."""
    executable = shutil.which(claude_cli.EXECUTABLE)
    if executable is None:
        raise LanguageModelError(f"No {claude_cli.EXECUTABLE!r} command on PATH.")
    args = claude_cli.command(
        executable,
        model,
        record["system"],
        record["schema"],
        tools=tools,
        read_usd=read_usd,
        effort=effort,
        fallback_model=str(record.get("fallback_model") or ""),
    )
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ish-replay-") as empty:
        body = claude_cli.run_whole(
            args,
            record["prompt"],
            cwd=record["tree"] if tools else empty,
            env=claude_cli.environment(os.environ, prompt_cache),
            timeout=timeout,
        )
    used = claude_cli.usage_of(body)
    failed = bool(body.get("is_error")) or "structured_output" not in body
    return {
        **{name: record[name] for name in FIELDS},
        "model": model,
        "tools": tools,
        "prompt_cache": prompt_cache,
        "read_usd": read_usd,
        "effort": effort,
        "seconds": round(time.perf_counter() - started, 3),
        "usage": {
            "uncached": used.uncached,
            "written": used.written,
            "cached": used.cached,
            "wrote": used.wrote,
        },
        "requests": used.requests,
        "cost_usd": used.cost_usd,
        "reply": body.get("structured_output"),
        "error": str(body.get("result")) if failed else "",
    }


def row(name: str, record: dict) -> str:
    """Render one call as a row of the table."""
    used = record.get("usage") or {}
    return (
        f"| {name} | {record['model']} | {record.get('prompt_cache', '-')} "
        f"| {record.get('requests', '-')} | {used.get('uncached', 0)} "
        f"| {used.get('written', 0)} | {used.get('cached', 0)} "
        f"| {used.get('wrote', 0)} | {record.get('cost_usd', 0):.4f} "
        f"| {record.get('seconds', 0):.1f} | {record.get('error', '')[:60]} |"
    )


HEADER = [
    "| call | model | cache | requests | uncached | written | read | wrote "
    "| USD | s | error |",
    "|---|---|---|---|---|---|---|---|---|---|---|",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("call", type=Path, help="One JSON file of a trace.")
    parser.add_argument("--model", help="Ask this model in place of the traced one.")
    parser.add_argument("--prompt-cache", choices=PROMPT_CACHES, default="5m")
    parser.add_argument("--read-usd", type=float, help="Cost cap of a call that reads.")
    parser.add_argument("--system", type=Path, help="Instructions to send in place.")
    parser.add_argument("--prompt", type=Path, help="Message to send in place.")
    parser.add_argument("--effort", choices=[e for e in EFFORTS if e])
    parser.add_argument("--no-tools", action="store_true", help="Read no file.")
    parser.add_argument("--repeat", type=int, default=1, help="Send the call N times.")
    parser.add_argument("--timeout", type=float, default=claude_cli.TIMEOUT_SECONDS)
    parser.add_argument("--trace", type=Path, help="Write each new call here.")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat takes a number of 1 or more.")
    try:
        record = load(args.call)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if record.get("resumed"):
        print(
            "This call resumed a session. Alone, it lacks the turns before it, "
            "and its message holds only what was new.",
            file=sys.stderr,
        )
    if args.system:
        record["system"] = args.system.read_text(encoding="utf-8")
    if args.prompt:
        record["prompt"] = args.prompt.read_text(encoding="utf-8")

    lines = [*HEADER, row("traced", record)]
    for number in range(1, args.repeat + 1):
        try:
            again = replay(
                record,
                model=args.model or record["model"],
                prompt_cache=args.prompt_cache,
                read_usd=args.read_usd or record.get("read_usd") or DEFAULT_READ_USD,
                tools=bool(record["tools"]) and not args.no_tools,
                timeout=args.timeout,
                effort=args.effort or str(record.get("effort") or ""),
            )
        except LanguageModelError as exc:
            print("\n".join(lines))
            print(f"\nCall {number} failed: {exc}", file=sys.stderr)
            return 1
        if args.trace:
            write_trace(args.trace, str(record["kind"]), again)
        lines.append(row(f"#{number}", again))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
