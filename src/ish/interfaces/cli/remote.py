"""The remote program of ish: search, or ask, the tree of a server.

``ish remote search QUERY`` prints the results of the server. ``ish
remote ask QUESTION`` gets chunks from the server and answers with the
language model of this machine. The server runs no model. Only a line
that starts with ``remote`` reaches this module, so a search of a local
tree does not import it.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from ish.interfaces.busy import Ticker
from ish.interfaces.cli.args import add_settings_options
from ish.interfaces.cli.main import report_step, show_answer, versions
from ish.interfaces.format import (
    answer_usage,
    format_answer,
    format_brief,
    format_usage,
)
from ish.interfaces.log import resolve_color, setup_logging
from ish.interfaces.python.remote import RemoteIsh
from ish.settings import CONFIG_OPTION, REMOTE_ENV_VAR, Settings, load_settings

log = logging.getLogger("ish.cli")

SEARCH = "search"
ASK = "ask"
COMMANDS = (SEARCH, ASK)

# What each command reads from the line, as its usage names it.
_TEXT = {SEARCH: "query", ASK: "question"}

# The settings that reach a server or the model of this machine. Every
# other setting is for a tree of this machine, which this program has none of.
OPTIONS = (
    "remote",
    "limit",
    "lang",
    "under",
    "type",
    "format",
    "color",
    "render",
    "verbosity",
    "language_model",
    "answer_model",
    "rerank_model",
    "rerank_candidates",
    "ask_turns",
    "prompt_dir",
    "allowed_language_models",
)

_USAGE = (
    "%(prog)s search QUERY [options]\n       %(prog)s ask QUESTION [--brief] [options]"
)

_DESCRIPTION = (
    "Search the tree of a server, or answer a question from it.\n"
    "\n"
    "  search QUERY    Print the results of the server.\n"
    "  ask QUESTION    Answer from the chunks of the server, with the\n"
    "                  language model of this machine.\n"
    "\n"
    f"--url, {REMOTE_ENV_VAR}, or `remote` in a config file names the server,\n"
    "in that order. The server searches its own tree, so the line takes no path."
)

_USE = "Use: ish remote search QUERY, or ish remote ask QUESTION"


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the whole line after ``ish remote``."""
    parser = argparse.ArgumentParser(
        prog="ish remote",
        usage=_USAGE,
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Read the command as a plain word, so that a line with a query and
    # no command gets an error that names `ish remote search`.
    parser.add_argument("command", nargs="?", default="", help=argparse.SUPPRESS)
    parser.add_argument("words", nargs="*", help=argparse.SUPPRESS)
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Answer in a sentence or two, with no sources.",
    )
    parser.add_argument(
        "-c",
        f"--{CONFIG_OPTION}",
        dest=CONFIG_OPTION,
        default=None,
        metavar="PATH",
        help="Read options from this file instead of searching upward for one.",
    )
    # The server knows the languages of its tree, so `--lang` takes any name.
    add_settings_options(
        parser, OPTIONS, renamed={"remote": "--url"}, any_value=("lang",)
    )
    return parser


def _problem(args: argparse.Namespace, settings: Settings) -> str:
    """Return what is wrong with the line, or an empty text."""
    no_server = f"No server is set. Give --url URL, or set {REMOTE_ENV_VAR}."
    if not args.command:
        return f"ish remote needs a command. {_USE}" if settings.remote else no_server
    if args.command not in COMMANDS:
        return f"{args.command!r} is not a command of ish remote. {_USE}"
    if not settings.remote:
        return no_server
    text = _TEXT[args.command]
    if not args.words or not args.words[0].strip():
        return f"ish remote {args.command} needs a {text}."
    if len(args.words) > 1:
        return (
            f"ish remote {args.command} takes one {text} and no path. "
            "The server searches its own tree. Use --under to narrow a search."
        )
    if args.brief and args.command != ASK:
        return "--brief is an option of ish remote ask."
    return ""


def search(
    settings: Settings,
    query: str,
    *,
    lang: Sequence[str] = (),
    under: str = "",
    type: Sequence[str] = (),
) -> int:
    """Print the results of the server for *query*. Return an exit code.

    Keep stdout for the results, and name the server on stderr, so a
    reader knows that the lines are not from a tree of this machine.
    """
    try:
        remote = RemoteIsh(settings)
        lines = remote.search(query, lang=lang, under=under, type=type)
    except Exception as exc:
        return _failed(exc)
    if lines:
        sys.stdout.write(f"{lines}\n")
        sys.stdout.flush()
    line = versions(remote.version, remote.host)
    if resolve_color(settings.color, sys.stderr):
        from ish.interfaces.cli.terminal import grey

        line = grey(line)
    sys.stderr.write(f"{line}\n")
    return 0


def ask(
    settings: Settings,
    question: str,
    *,
    brief: bool = False,
    lang: Sequence[str] = (),
    under: str = "",
    type: Sequence[str] = (),
) -> int:
    """Answer *question* from the chunks of the server. Return an exit code.

    Return 0 when the documents answer it, and 2 when they do not, as an
    answer from a local tree does. Show each step on stderr while the
    models work. End with the table of seconds and tokens, which names
    the server. Return 130 on Ctrl-C, with no traceback.
    """
    ticker = Ticker()
    try:
        remote = RemoteIsh(settings)
        answer = remote.ask(
            question,
            lang=lang,
            under=under,
            type=type,
            on_step=lambda step: report_step(ticker, step),
            brief=brief,
        )
    except KeyboardInterrupt:
        ticker.stop()
        sys.stderr.write("ish: interrupted\n")
        return 130
    except Exception as exc:
        ticker.stop()
        return _failed(exc)
    finally:
        ticker.stop()
    text = (
        format_brief(answer)
        if brief
        else format_answer(answer, question, remote.link, kind_of=remote.kind_of)
    )
    usage = format_usage(
        answer_usage(answer), versions=versions(remote.version, remote.host)
    )
    show_answer(settings, text, usage, brief=brief)
    return 0 if answer.covered else 2


def _failed(exc: Exception) -> int:
    """Report *exc* in one line, and return the exit code of a failure."""
    log.error("%s", exc)
    log.debug("Traceback:", exc_info=True)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Run the remote program from the command line. Return an exit code.

    Accept *argv* for testability. Default to ``sys.argv[1:]``.
    """
    # An option may stand between the command and its text, or after both.
    args = build_parser().parse_intermixed_args(argv)
    overrides = {name: getattr(args, name) for name in OPTIONS}
    overrides[CONFIG_OPTION] = args.config
    try:
        settings = load_settings(overrides, start=Path.cwd())
    except Exception as exc:
        # Logging is not configured yet, so report and stop.
        print(f"ish: {exc}", file=sys.stderr)
        return 1
    setup_logging(verbosity=settings.verbosity, color=resolve_color(settings.color))

    problem = _problem(args, settings)
    if problem:
        log.error("%s", problem)
        return 1
    # A filter travels only from this line. A filter in a config file of
    # this machine is for a tree of this machine.
    narrowing = {
        "lang": tuple(args.lang or ()),
        "under": args.under or "",
        "type": tuple(args.type or ()),
    }
    if args.command == ASK:
        return ask(settings, args.words[0], brief=args.brief, **narrowing)
    return search(settings, args.words[0], **narrowing)
