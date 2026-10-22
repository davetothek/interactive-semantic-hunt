"""Test the marker a query leaves, and the wait a document batch takes."""

import logging
from pathlib import Path

import pytest

from ish.adapters.embedder.turn import HOLD_SECONDS, POLL_SECONDS, Turn, default_turn
from ish.runtime import runtime_dir


class Clock:
    """A clock that moves only when something sleeps on it."""

    def __init__(self, at: float = 1000.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.at += seconds


def _turn(path: Path, clock: Clock) -> Turn:
    return Turn(path, clock=clock, sleep=clock.sleep)


class TestMark:
    def test_a_mark_leaves_the_file_in_a_private_directory(
        self, tmp_path: Path
    ) -> None:
        turn = _turn(tmp_path / "run" / "query", Clock())
        turn.mark()
        assert turn.path.is_file()
        assert (turn.path.parent.stat().st_mode & 0o777) == 0o700

    def test_a_fresh_mark_is_held(self, tmp_path: Path) -> None:
        clock = Clock()
        turn = _turn(tmp_path / "query", clock)
        turn.mark()
        clock.at += HOLD_SECONDS - POLL_SECONDS
        assert turn.held()

    def test_an_old_mark_is_not_held(self, tmp_path: Path) -> None:
        clock = Clock()
        turn = _turn(tmp_path / "query", clock)
        turn.mark()
        clock.at += HOLD_SECONDS
        assert not turn.held()

    def test_a_second_mark_renews_the_hold(self, tmp_path: Path) -> None:
        clock = Clock()
        turn = _turn(tmp_path / "query", clock)
        turn.mark()
        clock.at += HOLD_SECONDS - POLL_SECONDS
        turn.mark()
        clock.at += HOLD_SECONDS - POLL_SECONDS
        assert turn.held()

    def test_without_a_mark_nothing_is_held(self, tmp_path: Path) -> None:
        assert not _turn(tmp_path / "query", Clock()).held()

    def test_a_mark_that_cannot_be_written_costs_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A file where the directory should be refuses the marker."""
        blocker = tmp_path / "file"
        blocker.write_text("")
        turn = _turn(blocker / "query", Clock())
        with caplog.at_level(logging.DEBUG, logger="ish.adapters.embedder.turn"):
            turn.mark()
        assert not turn.held()
        assert "Cannot leave the query marker" in caplog.text


class TestWait:
    def test_the_wait_ends_when_the_mark_ages(self, tmp_path: Path) -> None:
        clock = Clock()
        turn = _turn(tmp_path / "query", clock)
        turn.mark()
        waited = turn.wait()
        assert waited == pytest.approx(HOLD_SECONDS, abs=POLL_SECONDS)
        assert clock.at - 1000.0 == pytest.approx(waited)
        assert not turn.held()

    def test_without_a_mark_there_is_no_wait(self, tmp_path: Path) -> None:
        clock = Clock()
        assert _turn(tmp_path / "query", clock).wait() == 0.0
        assert clock.at == 1000.0


class TestTheDefault:
    def test_every_process_of_the_user_shares_one_marker(self, tmp_path: Path) -> None:
        env = {"XDG_RUNTIME_DIR": str(tmp_path)}
        assert default_turn(env).path == runtime_dir(env) / "query"
