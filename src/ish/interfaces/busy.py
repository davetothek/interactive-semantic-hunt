"""Show that a command is busy, on one line of the terminal.

A first index of a large tree runs for minutes, and a command that
prints nothing cannot be told from one that has stopped. The line
turns a spinner, names the step, and while the model embeds says how
fast it goes and about how long is left. A blocked run and a slow one
otherwise look the same. Keep it to stderr, so a piped stdout still
holds only results.
"""

import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ish.application.progress import EMBED, Progress

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ASCII_FRAMES = "|/-\\"
MARK = "⠿"
ASCII_MARK = "*"

# How many chunks must be embedded before the rate means anything. One
# request of eight, so the first batch never sets a time.
MIN_DONE = 8


def _left(seconds: float) -> str:
    """Return *seconds* the way a person would say it, roughly."""
    if seconds < 90:
        return f"about {max(1, round(seconds))} s left"
    minutes = seconds / 60
    if minutes < 90:
        return f"about {round(minutes)} min left"
    return f"about {seconds / 3600:.1f} h left"


class Pacer:
    """Measure how fast the embedding goes, and say how long is left.

    Count from the first embedding step seen, so a run joined half way
    gets a rate from what it watched, not from a start it never saw.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tree: Path | None = None
        # The count and the clock at the first embedding step seen.
        self._began: tuple[int, float] | None = None

    def describe(self, step: Progress) -> str:
        """Return *step* as one line, with the pace while embedding."""
        text = str(step)
        if step.stage != EMBED:
            self._began = None
            return text
        # A count of zero, another tree, or a count that went back is
        # a new run, and the rate of the last one says nothing of it.
        if (
            self._began is None
            or step.done == 0
            or step.tree != self._tree
            or step.done < self._began[0]
        ):
            self._tree = step.tree
            self._began = (step.done, self._clock())
            return text
        first_done, began = self._began
        made = step.done - first_done
        elapsed = self._clock() - began
        if made < MIN_DONE or elapsed <= 0:
            return text
        rate = made / elapsed
        left = _left((step.total - step.done) / rate)
        return f"{text}, {rate:.1f} chunks/s, {left}"


class ProgressLine:
    """Rewrite one line of stderr with what a long run is doing.

    Write only to a terminal. A pipe gets nothing, because the log
    already carries each step at -v.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._pacer = Pacer(clock)
        self._frame = 0

    def show(self, step: Progress) -> None:
        """Rewrite the line with *step*, turning the spinner one notch."""
        if not sys.stderr.isatty():
            return
        text = f"{self._spin()} {self.render(step)}"
        sys.stderr.write(f"\r\033[2K{text[: _width() - 1]}")
        sys.stderr.flush()

    def render(self, step: Progress) -> str:
        """Return the line for *step*, with the pace while embedding."""
        return self._pacer.describe(step)

    def note(self, text: str) -> None:
        """Clear the line and leave *text* standing, marked as a notice."""
        if not sys.stderr.isatty():
            return
        sys.stderr.write(f"\r\033[2K{_mark()} {text}\n")
        sys.stderr.flush()

    def clear(self) -> None:
        """Clear the line, leaving the output as it would be."""
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[2K")
            sys.stderr.flush()

    def _spin(self) -> str:
        frames = _frames()
        frame = frames[self._frame % len(frames)]
        self._frame += 1
        return frame


class Ticker:
    """Keep one line of stderr turning while a step blocks for seconds.

    A call to a language model returns nothing until it is done, so a
    line drawn once would stand still and look stopped. A daemon thread
    redraws it every *interval* seconds with the time the step has run.
    Draw only on a terminal: a pipe gets nothing, and the log carries
    each step at -v.
    """

    def __init__(
        self,
        interval: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval
        self._clock = clock
        self._frame = 0
        self._text = ""
        self._since = clock()
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def show(self, text: str) -> None:
        """Name the step now running, and start turning on the first call."""
        with self._lock:
            self._text = text
            self._since = self._clock()
        if self._thread is None and sys.stderr.isatty():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        self.draw()

    def draw(self) -> None:
        """Rewrite the line with the step, the spinner, and the time it has run."""
        if not sys.stderr.isatty():
            return
        with self._lock:
            text, since = self._text, self._since
        frames = _frames()
        frame = frames[self._frame % len(frames)]
        self._frame += 1
        line = f"{frame} {text} · {self._clock() - since:.0f} s"
        sys.stderr.write(f"\r\033[2K{line[: _width() - 1]}")
        sys.stderr.flush()

    def stop(self) -> None:
        """Stop turning and clear the line, so the output stands alone.

        Stopping twice is harmless, so a caller may stop on an interrupt
        and again on its way out.
        """
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[2K")
            sys.stderr.flush()

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            self.draw()


def _width() -> int:
    """Return the terminal width, or a sensible guess."""
    return shutil.get_terminal_size((80, 24)).columns


def _frames() -> str:
    """Return the spinner frames the terminal can show."""
    return FRAMES if _can_show(FRAMES) else ASCII_FRAMES


def _mark() -> str:
    """Return the mark of a notice the terminal can show."""
    return MARK if _can_show(MARK) else ASCII_MARK


def _can_show(text: str) -> bool:
    """Return True when stderr can encode *text*."""
    encoding = getattr(sys.stderr, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True
