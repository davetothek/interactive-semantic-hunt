"""CLI entry point for ish.

Parse arguments, delegate wiring to the bootstrap module, format output.
"""

import logging
import os
import sys

from ish import bootstrap
from ish.interfaces.busy import ProgressLine, Ticker
from ish.interfaces.cli.args import CliArgs
from ish.interfaces.format import format_answer, format_selection, render
from ish.interfaces.log import resolve_color, setup_logging
from ish.interfaces.python.api import Ish

log = logging.getLogger("ish.cli")


def _run_query(args: CliArgs) -> int:
    """Search for the query and print the ranked results.

    Bring the index up to date first, behind a line that says what is
    happening, unless ``ish-serve`` keeps it current. The run then reads
    what the server wrote, and says so while the server is mid-refresh,
    because the answer may not hold the latest edit yet.
    """
    line = ProgressLine()
    with Ish(args.path, settings=args.settings) as ish:
        ish.index(on_progress=line.show)
        line.clear()
        kept = ish.kept_by()
        if kept is not None and kept.refreshing:
            line.note(f"ish-serve: {kept.refreshing}")
        for chunk, score in ish.search(args.query):
            sys.stdout.write(f"{render(chunk, args.settings.format, score)}\n")
    return 0


def _run_ask(args: CliArgs) -> int:
    """Answer the query as a question and print it as Markdown.

    Return 0 when the documents answer it, and 2 when they do not, so a
    script can tell the two apart. Show each step on stderr while the
    models work, and keep stdout for the answer, so it pipes into an
    editor. Return 130 on Ctrl-C, with no traceback.
    """
    if not args.query:
        log.error("--ask needs a question.")
        return 1
    line = ProgressLine()
    ticker = Ticker()
    with Ish(args.path, settings=args.settings) as ish:
        try:
            ish.index(on_progress=line.show)
            line.clear()
            answer = ish.ask(args.query, on_step=lambda step: ticker.show(str(step)))
        except KeyboardInterrupt:
            # Ctrl-C reaches a claude child as well, which stops with it.
            # Leave a clean line and the exit code a shell expects.
            line.clear()
            ticker.stop()
            sys.stderr.write("ish: interrupted\n")
            return 130
        finally:
            ticker.stop()
        text = format_answer(answer, args.query, ish.link, kind_of=ish.kind_of)
        sys.stdout.write(f"{text}\n")
    return 0 if answer.covered else 2


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


def _run_sweep(args: CliArgs) -> int:
    """Delete the vectors no index of the repository references, and say how many."""
    try:
        swept = bootstrap.sweep_vectors(args.settings, args.path)
    except LookupError as exc:
        log.error("%s", exc)
        return 1
    print(f"Swept {swept} vector(s) no index references.", file=sys.stderr)
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
    if args.settings.sweep:
        return _run_sweep(args)
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
        if args.ask:
            return _run_ask(args)
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
