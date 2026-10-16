"""Test ish-serve, the server that stays resident for a tree."""

import logging
import threading
import time
from pathlib import Path

import pytest

from ish.interfaces.mcp import daemon, transport
from ish.interfaces.mcp.protocol import Server
from ish.interfaces.mcp.server import IshTools
from ish.interfaces.mcp.transport import Client, SocketServer, socket_for
from ish.settings import Settings


class StubEmbedder:
    """Embed by length, so nothing here needs a daemon."""

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
    """A small tree to serve, with nothing written outside it."""
    import ish.bootstrap as bootstrap

    run = tmp_path_factory.mktemp("run")
    tree = tmp_path_factory.mktemp("tree")
    (tree / "app.py").write_text("def load_config():\n    return {}\n")
    monkeypatch.chdir(tree)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("ISH_NO_CACHE", "true")
    monkeypatch.setattr(transport, "ACCEPT_SECONDS", 0.02)
    monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", StubEmbedder)
    return tree


class Entry:
    """Run the entry point on a thread and keep its exit code."""

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.code: int | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.code = daemon.main(self.argv)

    def start(self) -> "Entry":
        self.thread.start()
        return self

    def join(self) -> int | None:
        self.thread.join(10)
        return self.code


def ask(path: Path) -> Client:
    client = Client.connect(path)
    assert client is not None
    return client


class TestServe:
    """Verify the entry point serves a socket for a tree."""

    def test_serves_the_working_directory_until_idle(self, tree: Path) -> None:
        entry = Entry(["--idle-seconds", "0.2"]).start()
        path = socket_for(tree)
        assert until(path.is_socket)

        with ask(path) as client:
            assert client.call("ping") == {}
            listed = client.call_tool("list_chunks", {"path": str(tree)})
            assert "load_config" in listed

        assert entry.join() == 0
        assert not path.exists()

    def test_a_tree_may_be_named(self, tree: Path, monkeypatch) -> None:
        monkeypatch.chdir(tree.parent)
        entry = Entry([str(tree), "--idle-seconds", "0.2"]).start()
        path = socket_for(tree)
        assert until(path.is_socket)
        with ask(path) as client:
            # A call that names no path means the tree served, not the
            # directory the server was started from.
            assert "load_config" in client.call_tool("list_chunks", {})
        assert entry.join() == 0

    def test_a_named_socket(self, tree: Path, tmp_path_factory) -> None:
        path = tmp_path_factory.mktemp("s") / "named.sock"
        entry = Entry(["--socket", str(path), "--idle-seconds", "0.2"]).start()
        assert until(path.is_socket)
        ask(path).close()
        assert entry.join() == 0

    def test_a_second_server_steps_aside(self, tree: Path) -> None:
        """Two shells may start a server at once. The second one leaves."""
        path = socket_for(tree)
        first = SocketServer(Server("ish", "1", []), path)
        thread = threading.Thread(target=first.serve_forever, daemon=True)
        thread.start()
        assert first.ready.wait(5)
        try:
            assert daemon.main([]) == 0
            assert path.is_socket()
        finally:
            first.shutdown()
            thread.join(5)

    def test_a_missing_tree_is_an_error(self, tree: Path, capsys) -> None:
        assert daemon.main([str(tree / "nowhere")]) == 1
        assert "is not a directory" in capsys.readouterr().err

    def test_an_interrupt_stops_the_server(self, tree: Path, monkeypatch) -> None:
        def interrupted(self) -> None:
            self.shutdown()
            raise KeyboardInterrupt

        monkeypatch.setattr(transport.SocketServer, "serve_forever", interrupted)
        assert daemon.main([]) == 0
        assert not socket_for(tree).exists()

    def test_verbose_logs_more(self, tree: Path) -> None:
        quiet = logging.getLogger("ish").level
        entry = Entry(["-v", "--idle-seconds", "0.2"]).start()
        assert until(socket_for(tree).is_socket)
        assert entry.join() == 0
        assert logging.getLogger("ish").level < quiet


class TestWatch:
    """Verify a resident server keeps its tree current from the start."""

    @pytest.fixture()
    def tools(self, tree: Path) -> IshTools:
        made = IshTools(Settings(no_cache=True, git=False), tree)
        yield made
        made.close()

    def test_watching_starts_the_refresh_at_once(
        self, tools: IshTools, tree: Path
    ) -> None:
        tools.watch(tree)
        assert tree in tools._watched
        assert any(watcher.is_alive() for watcher in tools._watchers)

    def test_watching_twice_starts_one_thread(
        self, tools: IshTools, tree: Path
    ) -> None:
        tools.watch(tree)
        tools.watch(tree)
        assert len(tools._watchers) == 1

    def test_many_threads_open_one_session(self, tools: IshTools, tree: Path) -> None:
        seen = []

        def open_one() -> None:
            seen.append(tools._session_for(tree))

        threads = [threading.Thread(target=open_one) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert len({id(session) for session in seen}) == 1
