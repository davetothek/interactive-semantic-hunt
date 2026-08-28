"""CLI entry point for ish.

Parse arguments, delegate wiring to the bootstrap module, format output.
"""

import logging
import sys

from ish import bootstrap
from ish.interfaces.cli.config import CliArgs
from ish.interfaces.cli.log import setup_logging
from ish.interfaces.format import (
    format_chunk_line,
    format_result_line,
    format_selection,
)

log = logging.getLogger("ish.cli")


def _run_query(args: CliArgs) -> int:
    """Search for the query and print the ranked results."""
    search_use_case = bootstrap.build_search(args.embedder)
    results = search_use_case.run(args.path, args.query, limit=5)
    for chunk, score in results:
        sys.stdout.write(f"{format_result_line(chunk, score)}\n")
    return 0


def _run_tui(args: CliArgs) -> int:
    """Run the interactive TUI and print the selection to stdout.

    Textual draws on stderr-safe terminal streams, so stdout stays
    reserved for the selection and supports `nvim $(ish -i src/)`.
    """
    from ish.interfaces.tui.app import IshApp

    search_use_case = bootstrap.build_search(args.embedder)
    app = IshApp(search_use_case, args.path)
    selected = app.run()

    if selected:
        chunk, _score = selected
        sys.stdout.write(f"{format_selection(chunk)}\n")
    return 0


def _run_scan(args: CliArgs) -> int:
    """Scan the path and list every chunk in the plain output format."""
    scanner = bootstrap.build_scan()
    for chunk in scanner.run(args.path):
        sys.stdout.write(f"{format_chunk_line(chunk)}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run ish from the command line. Return an exit code.

    Accept *argv* for testability. Default to ``sys.argv[1:]``.
    """
    args = CliArgs.from_args(argv)
    setup_logging(verbosity=args.verbosity, color=args.color)

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
            args.embedder,
            exc,
        )
    except Exception as exc:
        log.error("%s", exc)
        log.debug("Traceback:", exc_info=True)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
