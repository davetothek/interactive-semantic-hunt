"""CLI entry point for ish.

Parse arguments, delegate wiring to the bootstrap module, format output.
"""

import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from ish import bootstrap
from ish.application.progress import EMBED, Progress
from ish.interfaces.cli.args import CliArgs
from ish.interfaces.format import display_path, format_selection, render
from ish.interfaces.log import resolve_color, setup_logging
from ish.interfaces.python.api import Ish

log = logging.getLogger("ish.cli")

# The flags a resident server honors for one call. Any other flag
# changes what a run reads or writes, so that run does its own work.
_SERVER_FLAGS = frozenset(
    {"limit", "lang", "under", "type", "format", "verbosity", "color"}
)

# A result line in either shape: a score when ranked, then the path,
# then the line numbers. The path ends at the first colon a line number
# follows.
_RESULT_LINE = re.compile(
    r"^(?P<prefix>\[\d+\.\d+\] )?(?P<path>.+?):(?P<rest>\d+[-:].*)$"
)


class ProgressLine:
    """Show what a long refresh is doing, on one line of the terminal.

    A first index of a large tree runs for minutes, and a command that
    prints nothing cannot be told from one that has stopped. Keep it to
    stderr, so a piped stdout still holds only results. Say how fast
    the embedding goes, because a blocked run and a slow one otherwise
    look the same.
    """

    def __init__(self) -> None:
        self._embedding_began: float | None = None

    def show(self, step: Progress) -> None:
        """Rewrite the line with *step*."""
        if not sys.stderr.isatty():
            # Not a terminal, so the log already carries it at -v.
            return
        sys.stderr.write(f"\r\033[2K{self.render(step)[: _width() - 1]}")
        sys.stderr.flush()

    def render(self, step: Progress) -> str:
        """Return the line for *step*, with a rate while embedding."""
        text = str(step)
        if step.stage != EMBED:
            self._embedding_began = None
            return text
        if step.done == 0 or self._embedding_began is None:
            self._embedding_began = time.monotonic()
            return text
        elapsed = time.monotonic() - self._embedding_began
        if elapsed <= 0:
            return text
        return f"{text}, {step.done / elapsed:.1f} chunks/s"

    def clear(self) -> None:
        """Clear the line, leaving the output as it would be."""
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[2K")
            sys.stderr.flush()


def _width() -> int:
    """Return the terminal width, or a sensible guess."""
    return shutil.get_terminal_size((80, 24)).columns


def _run_query(args: CliArgs) -> int:
    """Search for the query and print the ranked results."""
    with Ish(args.path, settings=args.settings) as ish:
        for chunk, score in ish.search(args.query):
            sys.stdout.write(f"{render(chunk, args.settings.format, score)}\n")
    return 0


def _ask_server(args: CliArgs) -> int | None:
    """Answer through the server that listens for this tree, or return None.

    A resident server holds the index open and the model warm, so it
    answers in the time a process start takes. It answers from the
    index it holds and brings the index up to date behind the answer,
    so an edit is searchable moments later rather than embedded in
    front of this run. A run that refreshes, reindexes, names a config
    file, or sets an option the server cannot take per call does its
    own work. So does a run that finds no server, or a server that
    fails on the way.
    """
    given = {name for name, value in args.overrides.items() if value is not None}
    if not given <= _SERVER_FLAGS:
        return None

    from ish.interfaces.mcp import transport

    found = transport.find_socket(args.path)
    if found is None:
        return None
    client = transport.Client.connect(found)
    if client is None:
        return None

    arguments: dict[str, object] = {
        "query": args.query,
        "path": str(args.path),
        "limit": args.settings.limit,
        "format": args.settings.format,
    }
    if args.settings.lang:
        arguments["lang"] = list(args.settings.lang)
    if args.settings.under:
        arguments["under"] = args.settings.under
    if args.settings.type:
        arguments["type"] = list(args.settings.type)
    try:
        with client:
            text = client.call_tool("search_code", arguments)
            client.call_tool("refresh_index", {"path": str(args.path)})
    except transport.ToolError as exc:
        log.error("%s", exc)
        return 1
    except (OSError, transport.RpcError) as exc:
        log.info("Cannot use the server at %s: %s", found, exc)
        return None

    from ish.interfaces.mcp.server import NO_RESULTS

    log.info("Answered by the server at %s", found)
    if not text.startswith(NO_RESULTS):
        sys.stdout.write(f"{_relocate(text, args.path)}\n")
    return 0


def _relocate(text: str, base: Path) -> str:
    """Return *text* with each result path written from the working directory.

    The server writes a path from the directory asked about, which is
    what an editor resolves it against. The command line shows it from
    where the command ran, as a run of its own would. Leave a line that
    is not a result, and a path that is already absolute, as they are.
    """
    lines = []
    for line in text.splitlines():
        match = _RESULT_LINE.match(line)
        if match is None or Path(match["path"]).is_absolute():
            lines.append(line)
            continue
        shown = display_path(base / match["path"])
        lines.append(f"{match['prefix'] or ''}{shown}:{match['rest']}")
    return "\n".join(lines)


def _sync_terminal_size() -> None:
    """Tell Textual the real terminal size when stdout is not it.

    Textual measures the terminal through stdout, which
    `nvim $(ish -i src/)` redirects to a pipe for the selection. Reading
    COLUMNS and LINES comes first, before Textual ever queries stdout, so
    setting them from the terminal that stdin still faces keeps the
    picker sized to the real window instead of a frozen 80x24 fallback.
    """
    if sys.stdout.isatty():
        return
    for stream in (sys.stdin, sys.stderr):
        try:
            if not stream.isatty():
                continue
            size = os.get_terminal_size(stream.fileno())
        except OSError:
            continue
        os.environ.setdefault("COLUMNS", str(size.columns))
        os.environ.setdefault("LINES", str(size.lines))
        return


def _run_tui(args: CliArgs) -> int:
    """Run the interactive TUI and print the selection to stdout.

    Textual draws on stderr-safe terminal streams, so stdout stays
    reserved for the selection and supports `nvim $(ish -i src/)`.
    """
    from ish.interfaces.tui.app import IshApp

    _sync_terminal_size()
    with Ish(args.path, settings=args.settings) as ish:
        app = IshApp(
            ish,
            args.path,
            limit=args.settings.tui_limit,
            debounce_ms=args.settings.tui_debounce_ms,
            theme=args.settings.tui_theme,
        )
        selected = app.run()

    if selected:
        chunk, _score = selected
        sys.stdout.write(f"{format_selection(chunk)}\n")
    return 0


def _run_scan(args: CliArgs) -> int:
    """Scan the path and list every chunk in the plain output format."""
    for chunk in Ish(args.path, settings=args.settings).scan():
        sys.stdout.write(f"{render(chunk, args.settings.format)}\n")
    return 0


def _run_forget(args: CliArgs) -> int:
    """Remove the stored index of the path, and say what went.

    Run before the path is looked for on disk. The tree is usually gone,
    which is the reason to forget its index.
    """
    try:
        removed = bootstrap.forget_index(args.settings, args.path)
    except LookupError as exc:
        log.error("%s", exc)
        return 1
    print(f"Removed the index of {args.path}: {len(removed)} file(s).", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run ish from the command line. Return an exit code.

    Accept *argv* for testability. Default to ``sys.argv[1:]``.
    """
    try:
        args = CliArgs.from_args(argv)
    except Exception as exc:
        # Logging is not configured yet, so report and stop.
        print(f"ish: {exc}", file=sys.stderr)
        return 1

    setup_logging(
        verbosity=args.settings.verbosity, color=resolve_color(args.settings.color)
    )

    if args.settings.forget:
        return _run_forget(args)
    if not args.path.exists():
        log.error("Path does not exist: %s", args.path)
        return 1

    try:
        if args.settings.refresh:
            line = ProgressLine()
            bootstrap.refresh_indexes(
                args.settings,
                args.path,
                on_progress=line.show,
                overrides=args.overrides,
            )
            line.clear()
        if args.query:
            answered = _ask_server(args)
            if answered is not None:
                return answered
            return _run_query(args)
        if args.interactive:
            return _run_tui(args)
        return _run_scan(args)
    except ModuleNotFoundError as exc:
        log.error(
            "Embedder backend %r is not installed: %s. "
            "Install the matching optional dependency.",
            args.settings.embedder,
            exc,
        )
    except Exception as exc:
        log.error("%s", exc)
        log.debug("Traceback:", exc_info=True)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
