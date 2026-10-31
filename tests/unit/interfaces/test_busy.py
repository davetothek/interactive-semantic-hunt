"""Test the busy line of the terminal, and the pace it shows."""

import io
import sys
import threading
from pathlib import Path

import pytest

from ish.application.progress import EMBED, READY, REFRESH, Progress
from ish.interfaces import busy
from ish.interfaces.busy import Pacer, ProgressLine


def _tty(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(busy.sys.stderr, "isatty", lambda: on, raising=False)


def _step() -> Progress:
    return Progress(REFRESH).within(Path("/p/specification"), 2, 8)


class TestTheLine:
    """Verify the line says what a long refresh is doing.

    A command that prints nothing for minutes cannot be told from one
    that has stopped.
    """

    def test_a_terminal_sees_a_progress_line(self, monkeypatch, capsys) -> None:
        _tty(monkeypatch, True)
        line = ProgressLine()
        line.show(_step())
        line.clear()
        assert "Refreshing 2 of 8: specification" in capsys.readouterr().err

    def test_a_pipe_stays_quiet(self, monkeypatch, capsys) -> None:
        """The log already carries it, so do not write it twice."""
        _tty(monkeypatch, False)
        line = ProgressLine()
        line.show(_step())
        line.note("ish-serve: starting")
        line.clear()
        assert capsys.readouterr().err == ""

    def test_the_line_fits_the_terminal(self, monkeypatch, capsys) -> None:
        _tty(monkeypatch, True)
        monkeypatch.setattr(busy, "_width", lambda: 20)
        ProgressLine().show(Progress(EMBED, total=10**9, reused=10**9))
        written = capsys.readouterr().err
        assert len(written.replace("\r", "").replace("\033[2K", "")) < 20

    def test_the_spinner_turns(self, monkeypatch, capsys) -> None:
        _tty(monkeypatch, True)
        line = ProgressLine()
        line.show(_step())
        line.show(_step())
        written = capsys.readouterr().err.split("\r\033[2K")[1:]
        assert [text[0] for text in written] == [busy.FRAMES[0], busy.FRAMES[1]]

    def test_a_terminal_without_braille_gets_ascii(self, monkeypatch, capsys) -> None:
        _tty(monkeypatch, True)
        monkeypatch.setattr(busy, "_can_show", lambda text: False)
        line = ProgressLine()
        line.show(_step())
        line.note("done")
        written = capsys.readouterr().err
        assert written.isascii()
        assert busy.ASCII_FRAMES[0] in written
        assert busy.ASCII_MARK in written

    def test_a_notice_stays_on_a_line_of_its_own(self, monkeypatch, capsys) -> None:
        _tty(monkeypatch, True)
        line = ProgressLine()
        line.show(_step())
        line.note("ish-serve: Embedded 8 of 16 chunks")
        written = capsys.readouterr().err
        assert written.endswith(f"{busy.MARK} ish-serve: Embedded 8 of 16 chunks\n")


class TestWhatTheTerminalCanShow:
    def test_utf8_shows_braille(self, monkeypatch) -> None:
        monkeypatch.setattr(
            busy.sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        )
        assert busy._can_show(busy.FRAMES)

    def test_ascii_does_not(self, monkeypatch) -> None:
        monkeypatch.setattr(
            busy.sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        )
        assert not busy._can_show(busy.FRAMES)
        assert busy._can_show(busy.ASCII_FRAMES)

    def test_a_stream_without_an_encoding_is_taken_as_ascii(self, monkeypatch) -> None:
        monkeypatch.setattr(busy.sys, "stderr", io.StringIO())
        assert not busy._can_show(busy.MARK)

    def test_an_unknown_encoding_is_taken_as_ascii(self, monkeypatch) -> None:
        class Stream:
            encoding = "no-such-codec"

        monkeypatch.setattr(busy.sys, "stderr", Stream())
        assert not busy._can_show(busy.MARK)


class TestThePace:
    """Verify the rate and the time left. A blocked run and a slow one must differ."""

    def test_the_rate_and_the_time_left_are_shown(self) -> None:
        clock = iter([100.0, 110.0])
        pacer = Pacer(clock=lambda: next(clock))
        assert pacer.describe(Progress(EMBED, total=40)) == (
            "Embedding 40 chunks (0 reused)"
        )
        assert pacer.describe(Progress(EMBED, done=25, total=40)) == (
            "Embedded 25 of 40 chunks, 2.5 chunks/s, about 6 s left"
        )
        # Another stage ends the measurement.
        assert pacer.describe(Progress(READY, total=3)) == "Index ready: 3 files"

    def test_a_rate_needs_enough_chunks(self) -> None:
        """One batch says nothing. The first eight chunks set no time."""
        clock = iter([0.0, 1.0, 2.0])
        pacer = Pacer(clock=lambda: next(clock))
        assert (
            pacer.describe(Progress(EMBED, done=3, total=9)) == "Embedded 3 of 9 chunks"
        )
        assert (
            pacer.describe(Progress(EMBED, done=4, total=9)) == "Embedded 4 of 9 chunks"
        )
        assert pacer.describe(Progress(EMBED, done=11, total=20)) == (
            "Embedded 11 of 20 chunks, 4.0 chunks/s, about 2 s left"
        )

    def test_a_run_joined_half_way_counts_from_there(self) -> None:
        """The rate comes from what was watched, not from a start never seen."""
        clock = iter([50.0, 60.0])
        pacer = Pacer(clock=lambda: next(clock))
        pacer.describe(Progress(EMBED, done=100, total=300))
        assert pacer.describe(Progress(EMBED, done=130, total=300)) == (
            "Embedded 130 of 300 chunks, 3.0 chunks/s, about 57 s left"
        )

    def test_a_clock_that_stands_still_sets_no_rate(self) -> None:
        pacer = Pacer(clock=lambda: 5.0)
        pacer.describe(Progress(EMBED, total=40))
        assert pacer.describe(Progress(EMBED, done=20, total=40)) == (
            "Embedded 20 of 40 chunks"
        )

    def test_another_tree_starts_the_count_again(self) -> None:
        clock = iter([0.0, 10.0, 20.0])
        pacer = Pacer(clock=lambda: next(clock))
        a, b = Path("/p/a"), Path("/p/b")
        pacer.describe(Progress(EMBED, total=100).within(a, 1, 2))
        assert "chunks/s" in pacer.describe(
            Progress(EMBED, done=50, total=100).within(a, 1, 2)
        )
        assert pacer.describe(Progress(EMBED, done=10, total=100).within(b, 2, 2)) == (
            "Refreshing 2 of 2: b, Embedded 10 of 100 chunks"
        )

    def test_a_count_back_at_zero_starts_the_count_again(self) -> None:
        """A second run in one session begins at zero, at its own pace."""
        clock = iter([0.0, 10.0, 20.0, 25.0])
        pacer = Pacer(clock=lambda: next(clock))
        pacer.describe(Progress(EMBED, total=20))
        pacer.describe(Progress(EMBED, done=20, total=20))
        assert pacer.describe(Progress(EMBED, done=0, total=20)) == (
            "Embedding 20 chunks (0 reused)"
        )
        assert pacer.describe(Progress(EMBED, done=8, total=20)) == (
            "Embedded 8 of 20 chunks, 1.6 chunks/s, about 8 s left"
        )


class TestTimeLeft:
    @pytest.mark.parametrize(
        ("seconds", "said"),
        [
            (0.2, "about 1 s left"),
            (45, "about 45 s left"),
            (89, "about 89 s left"),
            (90, "about 2 min left"),
            (200, "about 3 min left"),
            (89 * 60, "about 89 min left"),
            (90 * 60, "about 1.5 h left"),
            (3 * 3600, "about 3.0 h left"),
        ],
    )
    def test_said_roughly(self, seconds, said) -> None:
        assert busy._left(seconds) == said


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestTicker:
    """A step that blocks for seconds still shows the time it has run."""

    def test_a_step_shows_its_name_and_its_time(self, monkeypatch) -> None:
        from ish.interfaces.busy import Ticker

        out = _Terminal()
        monkeypatch.setattr(sys, "stderr", out)
        now = [100.0]
        ticker = Ticker(interval=60, clock=lambda: now[0])
        ticker.show("haiku writing the answer")
        now[0] = 107.4
        ticker.draw()
        ticker.stop()
        drawn = out.getvalue()
        assert "haiku writing the answer · 0 s" in drawn
        assert "haiku writing the answer · 7 s" in drawn
        assert drawn.endswith("\r\033[2K")

    def test_a_new_step_restarts_the_time(self, monkeypatch) -> None:
        from ish.interfaces.busy import Ticker

        out = _Terminal()
        monkeypatch.setattr(sys, "stderr", out)
        now = [0.0]
        ticker = Ticker(interval=60, clock=lambda: now[0])
        ticker.show("Searching")
        now[0] = 5.0
        ticker.show("Reading 10 chunks")
        ticker.stop()
        assert "Reading 10 chunks · 0 s" in out.getvalue()

    def test_the_thread_redraws_until_it_is_stopped(self, monkeypatch) -> None:
        from ish.interfaces.busy import Ticker

        out = _Terminal()
        monkeypatch.setattr(sys, "stderr", out)
        ticker = Ticker(interval=0.001)
        drawn = threading.Event()
        original = ticker.draw

        def counting() -> None:
            original()
            if out.getvalue().count("Searching") > 2:
                drawn.set()

        monkeypatch.setattr(ticker, "draw", counting)
        ticker.show("Searching")
        assert drawn.wait(5)
        ticker.stop()
        assert ticker._thread is None

    def test_a_pipe_gets_nothing_and_no_thread_starts(self, monkeypatch) -> None:
        from ish.interfaces.busy import Ticker

        out = io.StringIO()
        monkeypatch.setattr(sys, "stderr", out)
        ticker = Ticker()
        ticker.show("Searching")
        ticker.stop()
        assert out.getvalue() == ""
        assert ticker._thread is None


def test_a_ticker_stops_twice_without_harm(monkeypatch) -> None:
    from ish.interfaces.busy import Ticker

    monkeypatch.setattr(sys, "stderr", _Terminal())
    ticker = Ticker(interval=60)
    ticker.show("Searching")
    ticker.stop()
    ticker.stop()
    assert ticker._thread is None


def test_a_notice_stays_above_the_turning_line(monkeypatch) -> None:
    from ish.interfaces.busy import Ticker

    out = _Terminal()
    monkeypatch.setattr(sys, "stderr", out)
    ticker = Ticker(interval=60)
    ticker.show("haiku writing the answer")
    ticker.note("Searching again: the handler")
    ticker.stop()
    assert "Searching again: the handler\n" in out.getvalue()


def test_a_notice_to_a_pipe_writes_nothing(monkeypatch) -> None:
    from ish.interfaces.busy import Ticker

    out = io.StringIO()
    monkeypatch.setattr(sys, "stderr", out)
    Ticker().note("Searching again: x")
    assert out.getvalue() == ""
