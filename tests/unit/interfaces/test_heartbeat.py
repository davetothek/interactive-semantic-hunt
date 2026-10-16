"""Test the status file ish-serve keeps written, and the nudge beside it."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from ish.interfaces import heartbeat
from ish.interfaces.heartbeat import Status, status_for


def _status(tree: Path, **more) -> Status:
    fields: dict = {"tree": tree, "pid": 4242, "updated": time.time()}
    fields.update(more)
    return Status(**fields)


class TestWhereTheFilesLive:
    def test_the_runtime_dir_is_used(self) -> None:
        where = heartbeat.runtime_dir({"XDG_RUNTIME_DIR": "/run/user/7"})
        assert where == Path("/run/user/7/ish")

    def test_without_a_runtime_dir_the_temp_dir_is_used(self) -> None:
        where = heartbeat.runtime_dir({})
        assert where.name.startswith("ish-")
        assert where.parent == Path(tempfile.gettempdir())

    def test_the_name_follows_the_tree(self, tmp_path: Path) -> None:
        path = status_for(tmp_path)
        assert path.name.startswith(tmp_path.name[: heartbeat.NAME_CHARS])
        assert path.suffix == ".status"
        assert heartbeat.nudge_for(tmp_path).suffix == ".refresh"
        assert heartbeat.nudge_for(tmp_path).stem == path.stem

    def test_a_long_name_is_cut(self, tmp_path: Path) -> None:
        name = status_for(tmp_path / ("x" * 100)).name
        assert len(name) == heartbeat.NAME_CHARS + len("-") + 12 + len(".status")

    def test_the_root_has_a_name(self) -> None:
        assert status_for(Path("/")).name.startswith("root-")

    def test_two_trees_of_one_name_stay_apart(self, tmp_path: Path) -> None:
        assert status_for(tmp_path / "a" / "proj") != status_for(
            tmp_path / "b" / "proj"
        )


class TestWriteAndRead:
    def test_what_is_written_is_read(self, tmp_path: Path) -> None:
        path = tmp_path / "run" / "x.status"
        status = _status(
            tmp_path,
            repository=tmp_path / ".git",
            checkout=tmp_path,
            refresh_seconds=30.0,
            refreshing="Reading 3 of 9 files",
            last_finished=12.5,
        )
        heartbeat.write(path, status)
        assert heartbeat.read(path) == status
        assert list(path.parent.glob("*.tmp")) == []

    def test_none_stays_none(self, tmp_path: Path) -> None:
        path = tmp_path / "x.status"
        heartbeat.write(path, _status(tmp_path))
        back = heartbeat.read(path)
        assert back is not None
        assert back.repository is None
        assert back.checkout is None
        assert back.refreshing is None
        assert back.last_finished is None

    def test_the_directory_is_private(self, tmp_path: Path) -> None:
        path = tmp_path / "run" / "x.status"
        heartbeat.write(path, _status(tmp_path))
        assert (path.parent.stat().st_mode & 0o777) == 0o700

    def test_a_missing_file_is_no_status(self, tmp_path: Path) -> None:
        assert heartbeat.read(tmp_path / "none") is None

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{",
            "[]",
            "42",
            '{"tree": "/x"}',
            '{"tree": "/x", "pid": "many", "updated": 1}',
        ],
    )
    def test_a_file_of_another_shape_is_no_status(self, tmp_path: Path, text) -> None:
        path = tmp_path / "x.status"
        path.write_text(text)
        assert heartbeat.read(path) is None

    def test_removing_takes_the_file_away_or_finds_none(self, tmp_path: Path) -> None:
        heartbeat.remove(tmp_path / "none")
        path = tmp_path / "x.status"
        path.write_text("{}")
        heartbeat.remove(path)
        assert not path.exists()


class TestFreshness:
    def test_a_recent_status_is_fresh(self, tmp_path: Path) -> None:
        status = _status(tmp_path, updated=1000.0)
        assert status.fresh(now=1000.0 + heartbeat.STALE_SECONDS - 1)

    def test_an_old_status_is_stale(self, tmp_path: Path) -> None:
        status = _status(tmp_path, updated=1000.0)
        assert not status.fresh(now=1000.0 + heartbeat.STALE_SECONDS)

    def test_the_clock_is_read_when_none_is_given(self, tmp_path: Path) -> None:
        assert _status(tmp_path).fresh()
        assert not _status(tmp_path, updated=0.0).fresh()


class TestWhatTheRefreshReaches:
    """Mirror the rule of a refresh: every tree below, except another checkout.

    The server refreshes the indexes `indexes_for()` returns for its
    tree, so a client inside a worktree of the same repository must
    not take the server for its own.
    """

    def test_the_tree_and_what_is_below_it(self, tmp_path: Path) -> None:
        status = _status(tmp_path)
        assert status.covers(tmp_path, None, None)
        assert status.covers(tmp_path / "a" / "b", None, None)
        assert not status.covers(tmp_path.parent, None, None)
        assert not status.covers(tmp_path.parent / "other", None, None)

    def test_the_same_checkout(self, tmp_path: Path) -> None:
        status = _status(tmp_path, repository=tmp_path / ".git", checkout=tmp_path)
        assert status.covers(tmp_path / "src", tmp_path / ".git", tmp_path)

    def test_another_checkout_of_the_same_repository_is_left_out(
        self, tmp_path: Path
    ) -> None:
        worktree = tmp_path / ".claude" / "worktrees" / "feature"
        status = _status(tmp_path, repository=tmp_path / ".git", checkout=tmp_path)
        assert not status.covers(worktree / "src", tmp_path / ".git", worktree)

    def test_a_nested_repository_is_reached(self, tmp_path: Path) -> None:
        sub = tmp_path / "spec"
        status = _status(tmp_path, repository=tmp_path / ".git", checkout=tmp_path)
        assert status.covers(sub, tmp_path / ".git" / "modules" / "spec", sub)

    def test_a_server_outside_git_reaches_everything_below(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        assert _status(tmp_path).covers(repo / "src", repo / ".git", repo)

    def test_a_path_outside_git_is_reached(self, tmp_path: Path) -> None:
        status = _status(tmp_path, repository=tmp_path / ".git", checkout=tmp_path)
        assert status.covers(tmp_path / "plain", None, None)


class TestFind:
    def test_the_server_of_the_tree_above_is_found(self, tmp_path: Path) -> None:
        heartbeat.write(status_for(tmp_path), _status(tmp_path))
        found = heartbeat.find(tmp_path / "a" / "b")
        assert found is not None
        assert found.tree == tmp_path
        assert heartbeat.find(tmp_path) is not None

    def test_a_stale_server_is_read_past(self, tmp_path: Path) -> None:
        heartbeat.write(status_for(tmp_path), _status(tmp_path, updated=0.0))
        assert heartbeat.find(tmp_path / "a") is None

    def test_a_server_whose_refresh_misses_the_path_is_read_past(
        self, tmp_path: Path
    ) -> None:
        worktree = tmp_path / "wt"
        status = _status(tmp_path, repository=tmp_path / ".git", checkout=tmp_path)
        heartbeat.write(status_for(tmp_path), status)
        assert heartbeat.find(worktree, tmp_path / ".git", worktree) is None
        assert heartbeat.find(worktree, tmp_path / ".git", tmp_path) is not None

    def test_nothing_is_found_without_a_server(self, tmp_path: Path) -> None:
        assert heartbeat.find(tmp_path) is None


class TestNudge:
    def test_a_nudge_is_left_and_taken(self, tmp_path: Path) -> None:
        path = heartbeat.nudge(tmp_path)
        assert path == heartbeat.nudge_for(tmp_path)
        assert path.exists()
        assert heartbeat.take_nudge(path) is True
        assert not path.exists()
        assert heartbeat.take_nudge(path) is False


class TestAlive:
    def test_this_process_is_alive(self) -> None:
        assert heartbeat.alive(os.getpid())

    def test_a_process_that_ended_is_dead(self) -> None:
        ended = subprocess.Popen([sys.executable, "-c", "pass"])
        ended.wait()
        assert not heartbeat.alive(ended.pid)

    def test_no_pid_is_dead(self) -> None:
        assert not heartbeat.alive(0)
        assert not heartbeat.alive(-1)

    def test_a_process_of_another_user_counts_as_alive(self, monkeypatch) -> None:
        def refused(pid, sig):
            raise PermissionError

        monkeypatch.setattr(heartbeat.os, "kill", refused)
        assert heartbeat.alive(12345)
