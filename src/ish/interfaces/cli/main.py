"""CLI entry point for ish.

Parse arguments, delegate wiring to the bootstrap module, format output.
"""

import logging
import sys

from ish import bootstrap
from ish.interfaces.cli.args import CliArgs
from ish.interfaces.cli.log import resolve_color, setup_logging
from ish.interfaces.format import (
    format_chunk_line,
    format_result_line,
    format_selection,
)

log = logging.getLogger("ish.cli")


def _run_query(args: CliArgs) -> int:
    """Search for the query and print the ranked results."""
    search_use_case = bootstrap.build_search(args.settings)
    results = search_use_case.run(args.path, args.query, limit=args.settings.limit)
    for chunk, score in results:
        sys.stdout.write(f"{format_result_line(chunk, score)}\n")
    return 0


def _run_tui(args: CliArgs) -> int:
    """Run the interactive TUI and print the selection to stdout.

    Textual draws on stderr-safe terminal streams, so stdout stays
    reserved for the selection and supports `nvim $(ish -i src/)`.
    """
    from ish.interfaces.tui.app import IshApp

    search_use_case = bootstrap.build_search(args.settings)
    app = IshApp(search_use_case, args.path, limit=args.settings.tui_limit)
    selected = app.run()

    if selected:
        chunk, _score = selected
        sys.stdout.write(f"{format_selection(chunk)}\n")
    return 0


def _run_scan(args: CliArgs) -> int:
    """Scan the path and list every chunk in the plain output format."""
    scanner = bootstrap.build_scan(args.settings)
    for chunk in scanner.run(args.path):
        sys.stdout.write(f"{format_chunk_line(chunk)}\n")
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
