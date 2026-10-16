"""Test ish-mcp joining a resident server.

A host spawns ``ish-mcp`` on stdio. When ``ish-serve`` runs for the
tree, ``ish-mcp`` carries the lines of its host to that server and
back, so the host shares one index and one warm model with everyone
else, and knows nothing about it.
"""

import io
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

from ish.interfaces.mcp import server as module
from ish.interfaces.mcp import transport
from ish.interfaces.mcp.protocol import Server, Tool
from ish.interfaces.mcp.transport import SocketServer, socket_for


class Recording:
    """A resident server whose one tool records what it is asked."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def server(self) -> Server:
        def handler(arguments):
            self.calls.append(dict(arguments))
            return f"from the resident server: {sorted(arguments)}"

        tool = Tool(
            name="search_code",
            description="Record.",
            schema={"type": "object"},
            handler=handler,
        )
        return Server("ish", "1", [tool])


@pytest.fixture()
def tree(tmp_path_factory, monkeypatch) -> Path:
    run = tmp_path_factory.mktemp("run")
    tree = tmp_path_factory.mktemp("tree")
    monkeypatch.chdir(tree)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("ISH_NO_CACHE", "true")
    return tree


@pytest.fixture()
def resident(tree: Path):
    recording = Recording()
    socket_server = SocketServer(recording.server(), socket_for(tree))
    thread = threading.Thread(target=socket_server.serve_forever, daemon=True)
    thread.start()
    assert socket_server.ready.wait(5)
    yield recording
    socket_server.shutdown()
    thread.join(5)


def call(name: str, arguments: dict | None = None, request_id: int = 1) -> dict:
    params: dict = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": params,
    }


def run_main(monkeypatch, messages: list[dict]) -> list[dict]:
    """Run ish-mcp over *messages* on stdin. Return every reply it wrote."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", out)
    assert module.main([]) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def text_of(reply: dict) -> str:
    return reply["result"]["content"][0]["text"]


class TestJoining:
    """Verify the host is served through the resident server."""

    def test_a_host_is_served_through_it(
        self, tree: Path, resident: Recording, monkeypatch
    ) -> None:
        replies = run_main(monkeypatch, [call("search_code", {"query": "q"})])
        assert len(replies) == 1
        assert text_of(replies[0]).startswith("from the resident server")
        assert len(resident.calls) == 1

    def test_a_call_that_names_no_path_means_the_hosts_directory(
        self, tree: Path, resident: Recording, monkeypatch
    ) -> None:
        run_main(monkeypatch, [call("search_code", {"query": "q"})])
        assert resident.calls[0]["path"] == str(tree)

    def test_a_call_with_a_path_keeps_it(
        self, tree: Path, resident: Recording, monkeypatch
    ) -> None:
        run_main(monkeypatch, [call("search_code", {"query": "q", "path": "/x"})])
        assert resident.calls[0]["path"] == "/x"

    def test_a_call_with_no_arguments_gets_the_path(
        self, tree: Path, resident: Recording, monkeypatch
    ) -> None:
        run_main(monkeypatch, [call("search_code")])
        assert resident.calls[0] == {"path": str(tree)}

    def test_every_line_goes_through_in_order(
        self, tree: Path, resident: Recording, monkeypatch
    ) -> None:
        replies = run_main(
            monkeypatch,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                call("search_code", {"query": "a"}, request_id=2),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                call("search_code", {"query": "b"}, request_id=3),
            ],
        )
        assert [reply["id"] for reply in replies] == [1, 2, 3]
        assert [c["query"] for c in resident.calls] == ["a", "b"]

    def test_an_interrupted_session_exits_cleanly(
        self, tree: Path, resident: Recording, monkeypatch
    ) -> None:
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(transport, "bridge", interrupted)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        assert module.main([]) == 0


class TestServingAlone:
    """Verify the process serves from itself when no server listens."""

    def test_without_a_server(self, tree: Path, monkeypatch) -> None:
        replies = run_main(monkeypatch, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        assert replies == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
        assert not socket_for(tree).exists()

    def test_a_dead_socket_is_removed_first(self, tree: Path, monkeypatch) -> None:
        path = socket_for(tree)
        path.parent.mkdir(parents=True)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()

        replies = run_main(monkeypatch, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        assert replies[0]["result"] == {}
        assert not path.exists()
