"""The loop of `ish chat` and `ish remote chat`: ask, read, and ask again.

Each line is a question, answered from the chunks a search finds, with a
citation for each claim. The chat keeps its history in memory while it
runs, and writes none of it to disk. A line that starts with `/` is a
command of the chat. A question that fails says why in one line, and the
chat goes on. Ctrl-C stops the reply that runs, and Ctrl-C or Ctrl-D at
the prompt ends the chat.
"""

import logging
import sys
from collections.abc import Callable
from typing import Any

from ish.application.answer import Answer
from ish.application.chat import Chat, Reading, Turn
from ish.interfaces.busy import Ticker
from ish.interfaces.format import display_path, format_chat

log = logging.getLogger("ish.cli")

PROMPT = "ish> "

HELP = """\
Ask a question about the files, and ask again from the answer.
  /more     the full answer of the last question, from what it read
  /sources  the chunks the last answer read
  /new      forget the chat so far
  /quit     end the chat, as Ctrl-D does
"""


def converse(
    session: Any,
    *,
    brief: bool,
    report: Callable[[Ticker, Any], None],
    read_line: Callable[[str], str] | None = None,
) -> int:
    """Run the chat on *session* until the person ends it. Return an exit code.

    Build the language model first, so a machine that allows no model
    stops before it reads any text, with exit code 1. Show each step of
    a reply through *report*. Read each line with *read_line*, or with
    ``input`` as it is when the chat starts.
    """
    read = read_line or input
    try:
        session.prepare(brief)
    except Exception as exc:
        log.error("%s", exc)
        return 1
    ticker: list[Ticker] = []

    def ask(asked: str, reading: Reading, short: bool) -> Answer:
        return session.ask(
            asked,
            on_step=lambda step: report(ticker[0], step),
            brief=short,
            reading=reading,
        )

    chat = Chat(ask, brief=brief)
    sys.stderr.write("Ask about the files. /help names the commands.\n")
    while True:
        try:
            line = read(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            return 0
        if not line:
            continue
        if not line.startswith("/"):
            _reply(session, ticker, lambda question=line: chat.ask(question))
            continue
        word = line.split()[0]
        if word == "/quit":
            return 0
        if word == "/help":
            sys.stdout.write(HELP)
        elif word == "/new":
            chat.new()
            sys.stderr.write("The chat starts again.\n")
        elif word == "/more":
            _reply(session, ticker, chat.more)
        elif word == "/sources":
            _sources(chat.last)
        else:
            sys.stderr.write(f"{word} is no command of the chat. /help names them.\n")


def _reply(session: Any, ticker: list[Ticker], run: Callable[[], Turn]) -> None:
    """Run one reply behind a busy line, and print it, or the reason it failed."""
    ticker[:] = [Ticker()]
    try:
        turn = run()
    except KeyboardInterrupt:
        ticker[0].stop()
        sys.stderr.write("ish: stopped\n")
        return
    except LookupError as exc:
        ticker[0].stop()
        sys.stderr.write(f"{exc}\n")
        return
    except Exception as exc:
        ticker[0].stop()
        log.error("%s", exc)
        log.debug("Traceback:", exc_info=True)
        return
    ticker[0].stop()
    text = format_chat(
        turn.answer,
        turn.question,
        session.link,
        brief=turn.brief,
        kind_of=session.kind_of,
    )
    sys.stdout.write(f"{text}\n\n")
    sys.stdout.flush()


def _sources(turn: Turn | None) -> None:
    """List the chunks the last answer read, or say there is none yet."""
    if turn is None:
        sys.stderr.write("There is no answer yet. Ask a question first.\n")
        return
    if not turn.answer.sources:
        sys.stderr.write("The last answer read no chunk.\n")
        return
    for n, chunk in enumerate(turn.answer.sources, 1):
        where = f"{display_path(chunk.path)}:{chunk.start_line}-{chunk.end_line}"
        sys.stdout.write(f"{n}. {where}  {chunk.kind}  {chunk.symbol}\n")
