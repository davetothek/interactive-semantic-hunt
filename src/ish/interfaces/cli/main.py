"""The search program of ish: search, answer, pick, and list.

Parse arguments, delegate wiring to the bootstrap module, format output.
``ish.interfaces.cli.run`` is the entry point, and hands a line here
unless it names a program of its own.
"""

import json
import logging
import os
import shutil
import sys
import time

from ish import bootstrap
from ish.application.answer import FOLLOWING, AnswerStep
from ish.interfaces.busy import ProgressLine, Ticker
from ish.interfaces.cli.args import CliArgs
from ish.interfaces.format import (
    answer_usage,
    format_answer,
    format_brief,
    format_selection,
    format_usage,
    render,
)
from ish.interfaces.log import resolve_color, setup_logging
from ish.interfaces.python.api import Ish
from ish.settings import MCP_TOKEN_ENV_VAR

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
    editor. End with a blank line and a table on stderr that says where
    the seconds and tokens went, and the version. Color what goes to a
    terminal. Return 130 on Ctrl-C, with no traceback.
    """
    if not args.query:
        log.error("%s needs a question.", "ish ask" if args.command else "--ask")
        return 1
    # The `ask` command answers on this machine. Only the flag follows
    # `remote` to a server.
    if args.settings.remote and not args.command:
        return _run_remote_ask(args)
    line = ProgressLine()
    ticker = Ticker()
    with Ish(args.path, settings=args.settings) as ish:
        try:
            ish.index(on_progress=line.show)
            line.clear()
            answer = ish.ask(
                args.query,
                on_step=lambda step: _report(ticker, step),
                brief=args.brief,
            )
        except KeyboardInterrupt:
            # Ctrl-C reaches a claude child as well, which stops with it.
            # Leave a clean line and the exit code a shell expects.
            line.clear()
            ticker.stop()
            sys.stderr.write("ish: interrupted\n")
            return 130
        finally:
            ticker.stop()
        text = (
            format_brief(answer)
            if args.brief
            else format_answer(answer, args.query, ish.link, kind_of=ish.kind_of)
        )
        _show(args, text, format_usage(answer_usage(answer), versions=_versions()))
    return 0 if answer.covered else 2


def _run_remote_ask(args: CliArgs) -> int:
    """Ask the server that ``remote`` names, and print its answer.

    Keep the exit codes of a local answer. The server searches its own
    tree, so the path given here does not travel. The filters do, and
    ``--ask-turns`` when it is given. Say
    how long the question took, and how much of it the server spent.
    """
    from ish.interfaces.mcp.client import RemoteClient, RemoteError

    settings = args.settings
    arguments: dict[str, object] = {"question": args.query, "brief": args.brief}
    if settings.lang:
        arguments["lang"] = list(settings.lang)
    if settings.under:
        arguments["under"] = settings.under
    if settings.type:
        arguments["type"] = list(settings.type)
    # The turns travel only when given on this command line. A value from
    # the config file here is for answers here, and the server has its own.
    if args.overrides.get("ask_turns") is not None:
        arguments["ask_turns"] = settings.ask_turns

    ticker = Ticker()
    start = time.monotonic()
    try:
        client = RemoteClient(settings.remote, os.environ.get(MCP_TOKEN_ENV_VAR, ""))
        ticker.show(f"Asking {client.host}")
        text = client.call("ask", arguments)
    except KeyboardInterrupt:
        ticker.stop()
        sys.stderr.write("ish: interrupted\n")
        return 130
    except RemoteError as exc:
        ticker.stop()
        log.error("%s", exc)
        return 1
    finally:
        ticker.stop()

    try:
        reply = json.loads(text)
    except ValueError:
        log.error("%s did not answer the question with JSON.", settings.remote)
        return 1
    shown = reply.get("answer") if args.brief else reply.get("markdown")
    if not isinstance(shown, str):
        log.error(
            "%s sent no Markdown for the answer. Update ish on the server.",
            settings.remote,
        )
        return 1
    usage = format_usage(
        reply, time.monotonic() - start, _versions(client.server_version)
    )
    _show(args, shown, usage)
    return 0 if reply.get("covered") else 2


def _versions(server: str = "") -> str:
    """Name the version of this ish, and of the server that answered."""
    from ish.interfaces.cli.args import installed_version

    mine = f"ish {installed_version()}"
    return f"{mine} · server {server}" if server else mine


def _show(args: CliArgs, answer: str, usage: str) -> None:
    """Print *answer* on stdout, and below it the *usage* table on stderr.

    Print the Markdown as it is, so a copy from the terminal is the text
    that a pipe gets. With ``render``, a terminal shows the full answer
    rendered, and a pipe still gets the Markdown. Grey the table when its
    stream takes color. Leave a blank line above the table, so it reads
    apart from the answer.
    """
    from ish.interfaces.cli.terminal import MAX_WIDTH, grey, rendered

    settings = args.settings
    if settings.render and not args.brief and sys.stdout.isatty():
        answer = rendered(
            answer,
            width=min(shutil.get_terminal_size().columns, MAX_WIDTH),
            color=resolve_color(settings.color, sys.stdout),
        )
    sys.stdout.write(f"{answer}\n")
    sys.stdout.flush()
    if resolve_color(settings.color, sys.stderr):
        usage = "\n".join(grey(line) for line in usage.split("\n"))
    sys.stderr.write(f"\n{usage}\n")


def _report(ticker: Ticker, step: AnswerStep) -> None:
    """Show *step* on the busy line, and leave a follow-up search standing."""
    if step.stage == FOLLOWING:
        ticker.note(str(step))
    ticker.show(str(step))


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


def _warn_of_replaced_flag(args: CliArgs) -> None:
    """Name the command that replaces ``--ask`` or ``--brief``, in one line.

    Say nothing for a question that goes to a server. No command replaces
    that one.
    """
    if args.command or not args.ask or args.settings.remote:
        return
    flag, use = (
        ("--brief", "ish ask QUESTION --brief")
        if args.brief
        else ("--ask", "ish ask QUESTION")
    )
    log.warning("%s goes away in a later release. Use: %s", flag, use)


def main(argv: list[str] | None = None) -> int:
    """Run the search program from the command line. Return an exit code.

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
    _warn_of_replaced_flag(args)

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
