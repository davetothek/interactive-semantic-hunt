"""CLI entry point for ish.

Parse arguments, delegate wiring to the bootstrap module, format output.
"""

import logging
import os
import shutil
import sys

from ish import bootstrap
from ish.application.search import parse_query
from ish.interfaces.cli.args import CliArgs
from ish.interfaces.cli.log import resolve_color, setup_logging
from ish.interfaces.format import (
    format_chunk_line,
    format_grep_line,
    format_result_line,
    format_selection,
)

log = logging.getLogger("ish.cli")


def _render(chunk, shape: str, score: float | None = None) -> str:
    """Render one result in the shape the caller asked for."""
    if shape == "grep":
        return format_grep_line(chunk, score)
    if score is None:
        return format_chunk_line(chunk)
    return format_result_line(chunk, score)


def _progress(message: str) -> None:
    """Show what a long refresh is doing, on one line.

    A first index of a large tree runs for minutes, and a command that
    prints nothing cannot be told from one that has stopped. Keep it to
    stderr, so a piped stdout still holds only results.
    """
    if not sys.stderr.isatty():
        # Not a terminal, so the log already carries it at -v.
        return
    sys.stderr.write(f"\r\033[2K{message[: _width() - 1]}")
    sys.stderr.flush()


def _progress_done() -> None:
    """Clear the progress line, leaving the output as it would be."""
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[2K")
        sys.stderr.flush()


def _width() -> int:
    """Return the terminal width, or a sensible guess."""
    return shutil.get_terminal_size((80, 24)).columns


def _run_query(args: CliArgs) -> int:
    """Search for the query and print the ranked results."""
    # Accept `lang:cpp type:doc` inside the query as well as as flags,
    # so a query copied from the interactive view behaves the same here.
    text, typed = parse_query(args.query)
    keep = bootstrap.build_result_filter(
        args.settings, typed.or_else(bootstrap.settings_filters(args.settings))
    )

    search_use_case = bootstrap.build_search(args.settings, args.path)
    try:
        results = search_use_case.run(
            args.path, text, limit=args.settings.limit, keep=keep
        )
        for chunk, score in results:
            sys.stdout.write(f"{_render(chunk, args.settings.format, score)}\n")
    finally:
        search_use_case.close()
    return 0


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
    search_use_case = bootstrap.build_search(args.settings, args.path)
    try:
        app = IshApp(
            search_use_case,
            args.path,
            limit=args.settings.tui_limit,
            debounce_ms=args.settings.tui_debounce_ms,
            filters=bootstrap.settings_filters(args.settings),
            categorize=bootstrap.build_categorizer(args.settings),
        )
        selected = app.run()
    finally:
        search_use_case.close()

    if selected:
        chunk, _score = selected
        sys.stdout.write(f"{format_selection(chunk)}\n")
    return 0


def _run_scan(args: CliArgs) -> int:
    """Scan the path and list every chunk in the plain output format."""
    scanner = bootstrap.build_scan(args.settings, args.path)
    keep = bootstrap.build_result_filter(
        args.settings, bootstrap.settings_filters(args.settings)
    )
    for chunk in scanner.run(args.path):
        if keep is None or keep(chunk):
            sys.stdout.write(f"{_render(chunk, args.settings.format)}\n")
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

    if not args.path.exists():
        log.error("Path does not exist: %s", args.path)
        return 1

    try:
        if args.settings.refresh:
            bootstrap.refresh_indexes(
                args.settings,
                args.path,
                on_progress=_progress,
                overrides=args.overrides,
            )
            _progress_done()
        if args.query:
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
