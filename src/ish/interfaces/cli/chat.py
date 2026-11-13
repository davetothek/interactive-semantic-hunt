"""The loop of `ish chat` and `ish remote chat`: ask, read, and ask again.

Each line is a question, answered from the chunks a search finds, with a
citation for each claim. The chat keeps its history in memory while it
runs, and writes none of it to disk. A line that starts with `/` is a
command of the chat. A question that fails says why in one line, and the
chat goes on. Ctrl-C stops the reply that runs, and Ctrl-C or Ctrl-D at
the prompt ends the chat.

On a terminal, Tab finishes a command and the arrows reach the questions
asked before, through `readline`, which keeps them in memory only. A
reply is Markdown, and a terminal shows it rendered. With color, a
question turns grey once it is sent, and so do the sources of a reply,
each a link to its file at its lines.
"""

import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit

from ish.application.answer import Answer
from ish.application.chat import Chat, Reading, Turn
from ish.domain.chunk import Chunk
from ish.interfaces.busy import Ticker
from ish.interfaces.cli.terminal import MAX_WIDTH, grey, rendered
from ish.interfaces.format import display_path, format_chat

log = logging.getLogger("ish.cli")

PROMPT = "ish> "

COMMANDS = ("/more", "/sources", "/open", "/new", "/help", "/quit")

HELP = """\
Ask a question about the files, and ask again from the answer.
  /more     the full answer of the last question, from what it read
  /sources  the chunks the last answer read
  /open N   open source N of the last reply in $EDITOR, or show its link
  /new      forget the chat so far
  /quit     end the chat, as Ctrl-D does
Tab finishes a command.
"""

# Move up a line, to its start, and clear it, so a sent question can be
# written again in grey where it was typed.
_UP = "\x1b[1A\r\x1b[2K"


def complete(text: str, state: int, line: str) -> str | None:
    """Return choice *state* of the commands that *text* starts, or None.

    Finish only the first word of a line that starts with ``/``: a word
    of a question is no command.
    """
    if not line.startswith("/") or " " in line[: len(line) - len(text)]:
        return None
    found = [command for command in COMMANDS if command.startswith(text)]
    return found[state] if state < len(found) else None


def _use_readline() -> None:
    """Let Tab finish a command, and the arrows reach earlier lines.

    The history of `readline` stays in memory: no file is read or written,
    because a question can quote a specification. A system with no
    `readline` reads lines as before.

    A Python built on libedit, as uv installs it, reads its own syntax
    for a key binding and ignores the one of GNU readline.
    """
    try:
        import readline
    except ImportError:
        return
    readline.set_completer(
        lambda text, state: complete(text, state, readline.get_line_buffer())
    )
    readline.set_completer_delims(" \t\n")
    if _is_libedit(readline):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def _is_libedit(module: Any) -> bool:
    """Return True when *module* is `readline` built on libedit.

    Python 3.13 names the library in ``backend``. Python 3.12 says it
    only in the text of the module.
    """
    backend = getattr(module, "backend", "")
    return backend == "editline" or "libedit" in (module.__doc__ or "")


class _Loop:
    """One chat on one terminal or stream, and what it shows."""

    def __init__(
        self,
        session: Any,
        chat: Chat,
        ticker: list[Ticker],
        *,
        color: bool,
        render: bool,
    ) -> None:
        self.session = session
        self.chat = chat
        self.ticker = ticker
        self.color = color
        self.render = render
        # The sources of the last reply, in the order of their numbers.
        self.cited: list[Chunk] = []

    def reply(self, run: Callable[[], Turn]) -> None:
        """Run one reply behind a busy line, and print it, or why it failed."""
        self.ticker[:] = [Ticker()]
        try:
            turn = run()
        except KeyboardInterrupt:
            self.ticker[0].stop()
            sys.stderr.write("ish: stopped\n")
            return
        except LookupError as exc:
            self.ticker[0].stop()
            sys.stderr.write(f"{exc}\n")
            return
        except Exception as exc:
            self.ticker[0].stop()
            log.error("%s", exc)
            log.debug("Traceback:", exc_info=True)
            return
        self.ticker[0].stop()
        text, self.cited = format_chat(
            turn.answer,
            turn.question,
            self.session.link,
            brief=turn.brief,
            kind_of=self.session.kind_of,
        )
        if self.render:
            text = rendered(
                text,
                width=min(shutil.get_terminal_size().columns, MAX_WIDTH),
                color=self.color,
                grey_sources=True,
            )
        sys.stdout.write(f"{text}\n\n")
        sys.stdout.flush()

    def sources(self) -> None:
        """List the chunks the last answer read, or say there is none yet."""
        turn = self.chat.last
        if turn is None:
            sys.stderr.write("There is no answer yet. Ask a question first.\n")
            return
        if not turn.answer.sources:
            sys.stderr.write("The last answer read no chunk.\n")
            return
        for n, chunk in enumerate(turn.answer.sources, 1):
            where = f"{display_path(chunk.path)}:{chunk.start_line}-{chunk.end_line}"
            sys.stdout.write(f"{n}. {where}  {chunk.kind}  {chunk.symbol}\n")

    def open(self, words: list[str]) -> None:
        """Open source N of the last reply in the editor, or show its link.

        A source on this machine opens at its first line in ``$VISUAL`` or
        ``$EDITOR``. With neither, or for a source of a server, which has
        no file here, show where it is instead.
        """
        if not self.cited:
            sys.stderr.write("The last reply names no source.\n")
            return
        count = len(self.cited)
        if len(words) != 2 or not words[1].isdigit() or not 1 <= int(words[1]) <= count:
            sys.stderr.write(
                f"Give the number of a source: /open 1 to /open {count}.\n"
            )
            return
        chunk = self.cited[int(words[1]) - 1]
        url = self.session.link(chunk)
        parts = urlsplit(url)
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if parts.scheme != "file" or not editor:
            sys.stdout.write(f"{url}\n")
            return
        path = unquote(parts.path)
        try:
            subprocess.run(
                [*shlex.split(editor), f"+{chunk.start_line}", path], check=False
            )
        except OSError as exc:
            log.error("Cannot start %s: %s", editor, exc)

    def sent(self, line: str) -> None:
        """Write the question just sent again, in grey, where it was typed."""
        width = shutil.get_terminal_size().columns
        rows = max(1, -(-(len(PROMPT) + len(line)) // width))
        sys.stdout.write(_UP * rows + grey(PROMPT + line) + "\n")


def converse(
    session: Any,
    *,
    brief: bool,
    report: Callable[[Ticker, Any], None],
    read_line: Callable[[str], str] | None = None,
    color: bool = False,
    render: bool = False,
    terminal: bool | None = None,
) -> int:
    """Run the chat on *session* until the person ends it. Return an exit code.

    Build the language model first, so a machine that allows no model
    stops before it reads any text, with exit code 1. Show each step of
    a reply through *report*. Read each line with *read_line*, or with
    ``input`` as it is when the chat starts. On a *terminal*, stdin by
    default, finish commands with Tab, and with *color* grey a sent
    question. With *render*, show each reply rendered, its sources in
    grey with *color*.
    """
    read = read_line or input
    terminal = sys.stdin.isatty() if terminal is None else terminal
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

    loop = _Loop(session, Chat(ask, brief=brief), ticker, color=color, render=render)
    if terminal:
        _use_readline()
    sys.stderr.write("Ask about the files. /help names the commands.\n")
    while True:
        try:
            line = read(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            return 0
        if not line:
            continue
        if terminal and color:
            loop.sent(line)
        if not line.startswith("/"):
            loop.reply(lambda question=line: loop.chat.ask(question))
            continue
        words = line.split()
        word = words[0]
        if word == "/quit":
            return 0
        if word == "/help":
            sys.stdout.write(HELP)
        elif word == "/new":
            loop.chat.new()
            loop.cited = []
            sys.stderr.write("The chat starts again.\n")
        elif word == "/more":
            loop.reply(loop.chat.more)
        elif word == "/sources":
            loop.sources()
        elif word == "/open":
            loop.open(words)
        else:
            sys.stderr.write(f"{word} is no command of the chat. /help names them.\n")
