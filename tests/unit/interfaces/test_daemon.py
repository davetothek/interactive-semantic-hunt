"""Test ish-serve, the process that keeps the index of a tree current."""

import logging
import os
import shutil
import threading
import time
from itertools import count
from pathlib import Path

import pytest

from ish import bootstrap
from ish.application.progress import EMBED, READ, Progress
from ish.application.search import WARM_QUERY
from ish.interfaces import daemon, heartbeat
from ish.interfaces.daemon import Keeper
from ish.settings import Settings


class StubEmbedder:
    """Embed by length, so nothing here needs a model."""

    model_name = "stub"

    @classmethod
    def from_option(cls, model: str, context_tokens=None) -> "StubEmbedder":
        return cls()

    def embed_documents(self, texts):
        return [[float(len(t)), 1.0] for t in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


def until(ready, seconds: float = 5.0) -> bool:
    """Wait for *ready* to hold, and say whether it did in time."""
    deadline = time.monotonic() + seconds
    while not ready() and time.monotonic() < deadline:
        time.sleep(0.01)
    return ready()


@pytest.fixture()
def tree(tmp_path_factory, monkeypatch) -> Path:
    """A small tree to keep current, with nothing written outside it."""
    tree = tmp_path_factory.mktemp("tree")
    (tree / "app.py").write_text("def load_config():\n    return {}\n")
    monkeypatch.chdir(tree)
    monkeypatch.setenv("ISH_NO_CACHE", "true")
    monkeypatch.setenv("ISH_GIT", "false")
    monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", StubEmbedder)
    return tree


@pytest.fixture()
def keeper(tree: Path):
    """A keeper that refreshes only when asked."""
    made = Keeper(Settings(no_cache=True, git=False, refresh_seconds=0), tree)
    yield made
    made.close()


def _quick(monkeypatch) -> None:
    """Make the keeper look for a nudge often, so a test waits for nothing."""
    monkeypatch.setattr(daemon, "NUDGE_SECONDS", 0.01)


class TestTheStatusFile:
    """Verify the file a client reads to know the server is alive."""

    def test_starting_claims_the_tree_and_a_refresh_stamps_it(
        self, keeper: Keeper, tree: Path
    ) -> None:
        keeper.start()
        status = heartbeat.read(keeper.status_path)
        assert status is not None
        assert status.tree == tree
        assert status.pid == os.getpid()
        assert status.refresh_seconds == 0.0
        assert status.refreshing is None
        assert status.last_finished is None

        keeper.refresh()
        after = heartbeat.read(keeper.status_path)
        assert after is not None
        assert after.refreshing is None
        assert after.last_finished is not None
        assert after.updated >= status.updated

    def test_the_status_says_what_the_refresh_is_doing(
        self, keeper: Keeper, tree: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(daemon, "WRITE_SECONDS", 0.0)
        seen: list[str | None] = []

        def talky(settings, root, on_progress=None, overrides=None, **kwargs):
            on_progress(Progress(READ, 3, 9).within(root, 1, 1))
            status = heartbeat.read(keeper.status_path)
            seen.append(None if status is None else status.refreshing)
            return [root]

        monkeypatch.setattr(bootstrap, "refresh_indexes", talky)
        keeper.refresh()
        assert seen == [f"Refreshing 1 of 1: {tree.name}, Reading 3 of 9 files"]

    def test_writes_while_progress_comes_in_are_bounded(
        self, keeper: Keeper, monkeypatch
    ) -> None:
        """Embedding reports every batch, and the file is not written for each."""
        written: list[str | None] = []
        monkeypatch.setattr(
            heartbeat, "write", lambda path, status: written.append(status.refreshing)
        )

        def talky(settings, root, on_progress=None, overrides=None, **kwargs):
            for done in range(0, 80, 8):
                on_progress(Progress(EMBED, done, 80))
            return [root]

        monkeypatch.setattr(bootstrap, "refresh_indexes", talky)
        keeper.refresh()
        assert written == ["starting", None]

    def test_the_beat_writes_while_nothing_changes(
        self, keeper: Keeper, monkeypatch
    ) -> None:
        """A long batch reports nothing, and the server must not read as dead."""
        monkeypatch.setattr(heartbeat, "BEAT_SECONDS", 0.01)
        keeper.start()
        first = heartbeat.read(keeper.status_path)
        assert first is not None

        def beaten() -> bool:
            status = heartbeat.read(keeper.status_path)
            return status is not None and status.updated > first.updated

        assert until(beaten)

    def test_a_status_that_cannot_be_written_is_reported(
        self, keeper: Keeper, monkeypatch, caplog
    ) -> None:
        def refused(path, status):
            raise OSError("read-only")

        monkeypatch.setattr(heartbeat, "write", refused)
        with caplog.at_level("WARNING", logger="ish"):
            keeper.write()
        assert "Cannot write the status" in caplog.text

    def test_closing_takes_the_status_away(self, keeper: Keeper) -> None:
        keeper.start()
        assert keeper.status_path.exists()
        keeper.close()
        assert not keeper.status_path.exists()


class TestTheCycle:
    """Verify when the server refreshes."""

    def test_a_nudge_starts_a_refresh(
        self, keeper: Keeper, tree: Path, monkeypatch
    ) -> None:
        _quick(monkeypatch)
        rounds: list[int] = []
        monkeypatch.setattr(
            bootstrap, "refresh_indexes", lambda *a, **k: rounds.append(1) or [tree]
        )
        thread = threading.Thread(target=keeper.serve_forever, daemon=True)
        thread.start()
        assert until(lambda: len(rounds) == 1)
        heartbeat.nudge(tree)
        assert until(lambda: len(rounds) == 2)
        assert not heartbeat.nudge_for(tree).exists()
        keeper.close()
        thread.join(5)
        assert not thread.is_alive()

    def test_the_interval_ends_the_wait(self, tree: Path, monkeypatch) -> None:
        _quick(monkeypatch)
        ticks = count(0.0, 0.5)
        keeper = Keeper(
            Settings(no_cache=True, git=False, refresh_seconds=1),
            tree,
            clock=lambda: next(ticks),
        )
        try:
            keeper._wait()
        finally:
            keeper.close()

    def test_an_interval_of_zero_waits_for_a_nudge(
        self, keeper: Keeper, tree: Path, monkeypatch
    ) -> None:
        _quick(monkeypatch)
        thread = threading.Thread(target=keeper._wait, daemon=True)
        thread.start()
        heartbeat.nudge(tree)
        thread.join(5)
        assert not thread.is_alive()

    def test_a_stop_ends_the_wait(self, keeper: Keeper, monkeypatch) -> None:
        _quick(monkeypatch)
        thread = threading.Thread(target=keeper._wait, daemon=True)
        thread.start()
        keeper.close()
        thread.join(5)
        assert not thread.is_alive()

    def test_a_failing_refresh_does_not_end_the_server(
        self, keeper: Keeper, monkeypatch, caplog
    ) -> None:
        def explode(*args, **kwargs):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(bootstrap, "refresh_indexes", explode)
        with caplog.at_level("WARNING", logger="ish"):
            keeper.refresh()
        assert "Cannot refresh" in caplog.text
        status = heartbeat.read(keeper.status_path)
        assert status is not None
        assert status.refreshing is None

    def test_a_refresh_keeps_the_model_warm(self, keeper: Keeper, monkeypatch) -> None:
        """The clients embed their queries, so the server pays the model load."""
        warmed: list[str] = []
        monkeypatch.setattr(
            StubEmbedder,
            "embed_query",
            lambda self, text: warmed.append(text) or [1.0, 1.0],
        )
        keeper.refresh()
        assert WARM_QUERY in warmed

    def test_a_backend_that_cannot_warm_is_noted(
        self, keeper: Keeper, tree: Path, monkeypatch, caplog
    ) -> None:
        def boom(settings):
            raise RuntimeError("no daemon")

        monkeypatch.setattr(bootstrap, "refresh_indexes", lambda *a, **k: [tree])
        monkeypatch.setattr(bootstrap, "build_embedder", boom)
        with caplog.at_level("INFO", logger="ish"):
            keeper.refresh()
        assert "Cannot warm" in caplog.text


class TestEntryPoint:
    """Verify the command line of the server."""

    def test_once_refreshes_and_leaves_nothing_behind(
        self, tree: Path, monkeypatch
    ) -> None:
        rounds: list[int] = []
        monkeypatch.setattr(
            bootstrap, "refresh_indexes", lambda *a, **k: rounds.append(1) or [tree]
        )
        assert daemon.main(["--once"]) == 0
        assert rounds == [1]
        assert not heartbeat.status_for(tree).exists()

    def test_a_tree_may_be_named(self, tree: Path, monkeypatch) -> None:
        monkeypatch.chdir(tree.parent)
        roots: list[Path] = []
        monkeypatch.setattr(
            bootstrap,
            "refresh_indexes",
            lambda settings, root, **k: roots.append(root) or [root],
        )
        assert daemon.main([str(tree), "--once"]) == 0
        assert roots == [tree]

    def test_a_second_server_steps_aside(self, tree: Path, monkeypatch) -> None:
        """Two shells may start a server at once. The second one leaves."""
        rounds: list[int] = []
        monkeypatch.setattr(
            bootstrap, "refresh_indexes", lambda *a, **k: rounds.append(1) or [tree]
        )
        monkeypatch.setattr(heartbeat, "alive", lambda pid: True)
        first = heartbeat.Status(tree=tree, pid=os.getpid() + 1, updated=time.time())
        heartbeat.write(heartbeat.status_for(tree), first)

        assert daemon.main(["--once"]) == 0
        assert rounds == []
        assert heartbeat.read(heartbeat.status_for(tree)) == first

    @pytest.mark.parametrize("dead", ["stale", "gone"])
    def test_a_dead_server_is_taken_over(self, tree: Path, monkeypatch, dead) -> None:
        rounds: list[int] = []
        monkeypatch.setattr(
            bootstrap, "refresh_indexes", lambda *a, **k: rounds.append(1) or [tree]
        )
        updated = 0.0 if dead == "stale" else time.time()
        if dead == "gone":
            monkeypatch.setattr(heartbeat, "alive", lambda pid: False)
        heartbeat.write(
            heartbeat.status_for(tree),
            heartbeat.Status(tree=tree, pid=os.getpid() + 1, updated=updated),
        )
        assert daemon.main(["--once"]) == 0
        assert rounds == [1]

    def test_a_missing_tree_is_an_error(self, tree: Path, capsys) -> None:
        assert daemon.main([str(tree / "nowhere")]) == 1
        assert "is not a directory" in capsys.readouterr().err

    def test_an_interrupt_stops_the_server(self, tree: Path, monkeypatch) -> None:
        def interrupted(self) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(Keeper, "serve_forever", interrupted)
        assert daemon.main([]) == 0
        assert not heartbeat.status_for(tree).exists()

    def test_a_termination_signal_leaves_like_an_interrupt(self) -> None:
        with pytest.raises(KeyboardInterrupt):
            daemon._exit_on_signal(15, None)

    def test_the_handler_is_installed_on_the_main_thread_only(
        self, tree: Path, monkeypatch
    ) -> None:
        installed: list[object] = []
        monkeypatch.setattr(
            daemon.signal,
            "signal",
            lambda signum, handler: installed.append(handler),
        )
        monkeypatch.setattr(bootstrap, "refresh_indexes", lambda *a, **k: [tree])

        assert daemon.main(["--once"]) == 0
        assert installed == [daemon._exit_on_signal]

        installed.clear()
        thread = threading.Thread(target=lambda: daemon.main(["--once"]))
        thread.start()
        thread.join(10)
        assert installed == []

    def test_verbose_logs_more(self, tree: Path, monkeypatch) -> None:
        quiet = logging.getLogger("ish").level
        monkeypatch.setattr(bootstrap, "refresh_indexes", lambda *a, **k: [tree])
        assert daemon.main(["-v", "--once"]) == 0
        assert logging.getLogger("ish").level < quiet


class TestOneServerPerRepository:
    """Verify the checkouts of one repository get one server between them.

    A worktree is a second checkout, inside the main tree or beside it.
    The server of either keeps both current, and a second server for
    any checkout of the repository leaves.
    """

    @pytest.fixture()
    def repository(self, tree: Path, monkeypatch) -> Path:
        """Make the tree a checkout of a repository, without git."""
        common = tree.parent / "main" / ".git"
        monkeypatch.setattr(
            bootstrap, "repository_identity", lambda root: (common, root)
        )
        return common

    def test_a_server_of_another_checkout_keeps_this_tree(
        self, tree: Path, repository: Path, monkeypatch
    ) -> None:
        rounds: list[int] = []
        monkeypatch.setattr(
            bootstrap, "refresh_indexes", lambda *a, **k: rounds.append(1) or [tree]
        )
        monkeypatch.setattr(heartbeat, "alive", lambda pid: True)
        main = tree.parent / "main"
        heartbeat.write(
            heartbeat.status_for(main),
            heartbeat.Status(
                tree=main,
                pid=os.getpid() + 1,
                updated=time.time(),
                repository=repository,
                checkout=main,
            ),
        )
        assert daemon.main(["--once"]) == 0
        assert rounds == []

    def test_a_server_of_another_repository_does_not(
        self, tree: Path, repository: Path, monkeypatch
    ) -> None:
        rounds: list[int] = []
        monkeypatch.setattr(
            bootstrap, "refresh_indexes", lambda *a, **k: rounds.append(1) or [tree]
        )
        monkeypatch.setattr(heartbeat, "alive", lambda pid: True)
        other = tree.parent / "other"
        heartbeat.write(
            heartbeat.status_for(other),
            heartbeat.Status(
                tree=other,
                pid=os.getpid() + 1,
                updated=time.time(),
                repository=other / ".git",
                checkout=other,
            ),
        )
        assert daemon.main(["--once"]) == 0
        assert rounds == [1]

    def test_the_refresh_visits_every_checkout(
        self, keeper: Keeper, monkeypatch
    ) -> None:
        asked: dict = {}

        def record(settings, root, on_progress=None, overrides=None, **kwargs):
            asked.update(kwargs)
            return [root]

        monkeypatch.setattr(bootstrap, "refresh_indexes", record)
        keeper.refresh()
        assert asked == {"checkouts": True}

    def test_a_server_stops_when_its_tree_is_gone(
        self, tree: Path, monkeypatch, caplog
    ) -> None:
        """A server that went on would prune the index and write for ever."""
        _quick(monkeypatch)
        monkeypatch.chdir(tree.parent)
        monkeypatch.setattr(bootstrap, "refresh_indexes", lambda *a, **k: [tree])
        keeper = Keeper(Settings(no_cache=True, git=False, refresh_seconds=0), tree)
        thread = threading.Thread(target=keeper.serve_forever, daemon=True)
        try:
            with caplog.at_level("WARNING", logger="ish"):
                thread.start()
                shutil.rmtree(tree)
                heartbeat.nudge(tree)
                thread.join(5)
            assert not thread.is_alive()
            assert "is gone. Stopping" in caplog.text
        finally:
            keeper.close()
