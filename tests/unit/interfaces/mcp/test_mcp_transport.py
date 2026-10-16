"""Test the Unix socket transport of the MCP interface.

One server answers every client of a tree, and outlives any of them.
The tests here drive a real socket in a temporary directory, with a
server of two tools on its own thread.
"""

import io
import itertools
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from ish.interfaces.mcp import transport
from ish.interfaces.mcp.protocol import Server, Tool
from ish.interfaces.mcp.transport import (
    Client,
    RpcError,
    ServerRunning,
    SocketServer,
    ToolError,
    find_socket,
    runtime_dir,
    socket_for,
)

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


def echo_tool() -> Tool:
    return Tool(
        name="echo",
        description="Echo the argument back.",
        schema=SCHEMA,
        handler=lambda args: f"got {args.get('x')}",
    )


def failing_tool() -> Tool:
    def fail(args):
        raise ValueError("the tool broke")

    return Tool(name="fail", description="Fail.", schema=SCHEMA, handler=fail)


def where_tool() -> Tool:
    return Tool(
        name="where",
        description="Say which path was asked about.",
        schema=SCHEMA,
        handler=lambda args: str(args.get("path")),
    )


def build() -> Server:
    return Server("ish", "1.2.3", [echo_tool(), failing_tool(), where_tool()])


def call_line(name: str, arguments: dict | None = None, request_id: int = 1) -> str:
    params: dict = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    message = {"jsonrpc": "2.0", "id": request_id, "method": "tools/call"}
    return json.dumps({**message, "params": params}) + "\n"


def text_of(line: str) -> str:
    return json.loads(line)["result"]["content"][0]["text"]


def until(ready, seconds: float = 5.0) -> bool:
    """Wait for *ready* to hold, and say whether it did in time."""
    deadline = time.monotonic() + seconds
    while not ready() and time.monotonic() < deadline:
        time.sleep(0.01)
    return ready()


class Running:
    """A socket server on its own thread, stopped at the end of a test."""

    def __init__(self, path: Path, **options) -> None:
        self.server = SocketServer(build(), path, **options)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "Running":
        self.thread.start()
        assert self.server.ready.wait(5)
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(5)


def connected(path: Path) -> Client:
    client = Client.connect(path)
    assert client is not None
    return client


@pytest.fixture()
def sock(tmp_path_factory) -> Path:
    # A socket path is limited to about a hundred bytes, so keep the
    # test's name out of it.
    return tmp_path_factory.mktemp("s") / "ish.sock"


@pytest.fixture()
def running(sock: Path):
    server = Running(sock).start()
    yield server
    server.stop()


class TestCalls:
    """Verify a client reaches the tools through the socket."""

    def test_a_client_calls_a_tool(self, running: Running, sock: Path) -> None:
        with connected(sock) as client:
            assert client.call_tool("echo", {"x": "hi"}) == "got hi"

    def test_two_clients_share_the_server(self, running: Running, sock: Path) -> None:
        first = connected(sock)
        second = connected(sock)
        try:
            assert second.call_tool("echo", {"x": "2"}) == "got 2"
            assert first.call_tool("echo", {"x": "1"}) == "got 1"
        finally:
            first.close()
            second.close()

    def test_a_client_may_ask_many_times(self, running: Running, sock: Path) -> None:
        with connected(sock) as client:
            assert [client.call("ping") for _ in range(3)] == [{}, {}, {}]

    def test_an_error_reply_raises(self, running: Running, sock: Path) -> None:
        with connected(sock) as client, pytest.raises(RpcError, match="Unknown"):
            client.call("no/such")

    def test_a_tool_failure_raises(self, running: Running, sock: Path) -> None:
        with (
            connected(sock) as client,
            pytest.raises(ToolError, match="the tool broke"),
        ):
            client.call_tool("fail", {})

    def test_a_connection_that_fails_is_logged(
        self, running: Running, sock: Path, monkeypatch
    ) -> None:
        """A client that breaks its connection must not break the server."""

        def broken(reader, writer):
            raise OSError("the pipe broke")

        monkeypatch.setattr(running.server._server, "serve", broken)
        client = connected(sock)
        client.close()
        assert until(lambda: running.server._connected == 0)


class TestHangUps:
    """Verify a client notices a server that does not speak the protocol."""

    def _server_that(
        self, sock: Path, behave
    ) -> tuple[socket.socket, threading.Thread]:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sock))
        listener.listen()

        def once() -> None:
            connection, _address = listener.accept()
            with connection:
                behave(connection)

        thread = threading.Thread(target=once, daemon=True)
        thread.start()
        return listener, thread

    def test_a_server_that_hangs_up(self, sock: Path) -> None:
        """Read the request, then close. The client sees the end of the stream."""
        listener, thread = self._server_that(
            sock, lambda connection: connection.recv(4096)
        )
        try:
            with connected(sock) as client, pytest.raises(OSError, match="hung up"):
                client.call("ping")
        finally:
            thread.join(5)
            listener.close()

    def test_a_server_that_speaks_garbage(self, sock: Path) -> None:
        def talk(connection: socket.socket) -> None:
            connection.recv(4096)
            connection.sendall(b"not json\n")

        listener, thread = self._server_that(sock, talk)
        try:
            with (
                connected(sock) as client,
                pytest.raises(RpcError, match="not a reply"),
            ):
                client.call("ping")
        finally:
            thread.join(5)
            listener.close()

    def test_a_reply_that_is_not_an_object(self, sock: Path) -> None:
        def talk(connection: socket.socket) -> None:
            connection.recv(4096)
            connection.sendall(b"[1]\n")

        listener, thread = self._server_that(sock, talk)
        try:
            with (
                connected(sock) as client,
                pytest.raises(RpcError, match="not a reply"),
            ):
                client.call("ping")
        finally:
            thread.join(5)
            listener.close()


class TestSocketFiles:
    """Verify the file comes and goes with the server."""

    def test_the_file_goes_when_the_server_stops(self, sock: Path) -> None:
        running = Running(sock).start()
        assert sock.is_socket()
        running.stop()
        assert not sock.exists()

    def test_a_stale_file_is_taken_over(self, sock: Path) -> None:
        """A killed server leaves its file. Nothing answers there."""
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(sock))
        stale.close()
        assert sock.exists()

        running = Running(sock).start()
        try:
            connected(sock).close()
        finally:
            running.stop()

    def test_a_live_socket_is_left_alone(self, running: Running, sock: Path) -> None:
        with pytest.raises(ServerRunning, match="already listens"):
            SocketServer(build(), sock).serve_forever()
        assert sock.is_socket()
        with connected(sock) as client:
            assert client.call("ping") == {}

    def test_another_bind_failure_is_raised(self, tmp_path_factory) -> None:
        too_long = tmp_path_factory.mktemp("s") / ("n" * 120 + ".sock")
        with pytest.raises(OSError):
            SocketServer(build(), too_long).serve_forever()

    def test_connect_removes_a_stale_file(self, sock: Path) -> None:
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(sock))
        stale.close()
        assert Client.connect(sock) is None
        assert not sock.exists()

    def test_connect_to_nothing(self, sock: Path) -> None:
        assert Client.connect(sock) is None

    def test_answers_says_no_for_a_missing_socket(self, sock: Path) -> None:
        assert transport.answers(sock) is False

    def test_shutdown_twice_is_safe(self, sock: Path) -> None:
        running = Running(sock).start()
        running.stop()
        running.server.shutdown()


class TestNaming:
    """Verify a client and a server agree on where the socket is."""

    def test_the_socket_is_named_after_the_tree(self, tmp_path: Path) -> None:
        env = {"XDG_RUNTIME_DIR": str(tmp_path)}
        path = socket_for(tmp_path / "proj", env)
        assert path.parent == tmp_path / "ish"
        assert path.name.startswith("proj-")
        assert path.name.endswith(".sock")
        assert socket_for(tmp_path / "proj", env) == path
        assert socket_for(tmp_path / "other", env) != path

    def test_a_long_name_is_cut(self, tmp_path: Path) -> None:
        name = socket_for(tmp_path / ("n" * 100), {"XDG_RUNTIME_DIR": "/run"}).name
        assert len(name) == transport.NAME_CHARS + len("-") + 12 + len(".sock")

    def test_the_root_of_the_filesystem_has_a_name(self) -> None:
        assert socket_for(Path("/"), {"XDG_RUNTIME_DIR": "/run"}).name.startswith(
            "root-"
        )

    def test_without_a_runtime_dir_the_temp_dir_is_used(self) -> None:
        where = runtime_dir({})
        assert where.parent == Path(tempfile.gettempdir())
        assert where.name == f"ish-{os.getuid()}"

    def test_find_socket_walks_up(self, tmp_path_factory) -> None:
        run = tmp_path_factory.mktemp("run")
        tree = tmp_path_factory.mktemp("tree")
        (tree / "a" / "b").mkdir(parents=True)
        env = {"XDG_RUNTIME_DIR": str(run)}
        path = socket_for(tree, env)

        running = Running(path).start()
        try:
            assert find_socket(tree / "a" / "b", env) == path
            assert find_socket(tree, env) == path
        finally:
            running.stop()
        assert find_socket(tree / "a", env) is None


class TestIdle:
    """Verify a server nobody uses stops, and one in use does not."""

    def test_zero_means_never(self, sock: Path) -> None:
        """An age passes between the start and the question."""
        ages = itertools.count(0.0, 1e9)
        never = SocketServer(build(), sock, idle_seconds=0.0, clock=ages.__next__)
        assert never._idle() is False
        soon = SocketServer(build(), sock, idle_seconds=5.0, clock=ages.__next__)
        assert soon._idle() is True

    def test_the_server_stops_when_nobody_comes(self, sock: Path, monkeypatch) -> None:
        monkeypatch.setattr(transport, "ACCEPT_SECONDS", 0.01)
        now = [0.0]

        def clock() -> float:
            now[0] += 100.0
            return now[0]

        server = SocketServer(build(), sock, idle_seconds=5.0, clock=clock)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        assert not sock.exists()

    def test_a_connected_client_keeps_it_alive(self, sock: Path, monkeypatch) -> None:
        monkeypatch.setattr(transport, "ACCEPT_SECONDS", 0.01)
        later = threading.Event()
        now = [0.0]

        def clock() -> float:
            # Time stands still until the test lets it run, and then an
            # age passes between any two looks at it.
            if later.is_set():
                now[0] += 100.0
            return now[0]

        server = SocketServer(build(), sock, idle_seconds=5.0, clock=clock)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        assert server.ready.wait(5)

        with connected(sock) as client:
            assert client.call("ping") == {}
            assert thread.is_alive()
            later.set()
            # Connected, so the passing time changes nothing.
            assert client.call("ping") == {}
            assert thread.is_alive()
        thread.join(5)
        assert not thread.is_alive()


class TestBridge:
    """Verify a host on stdio is carried to a server on a socket."""

    def _bridged(self, sock: Path, stdin: str, default_path: Path | None = None):
        connection = transport.connect(sock)
        assert connection is not None
        out = io.StringIO()
        transport.bridge(io.StringIO(stdin), out, connection, default_path=default_path)
        return out.getvalue().splitlines()

    def test_lines_go_both_ways(self, running: Running, sock: Path) -> None:
        lines = self._bridged(sock, call_line("echo", {"x": "hi"}))
        assert [text_of(line) for line in lines] == ["got hi"]

    def test_the_session_ends_when_the_host_does(
        self, running: Running, sock: Path
    ) -> None:
        """Two requests, then the end of stdin: two replies, then the return."""
        stdin = call_line("echo", {"x": "1"}, 1) + call_line("echo", {"x": "2"}, 2)
        lines = self._bridged(sock, stdin)
        assert [json.loads(line)["id"] for line in lines] == [1, 2]
        assert until(lambda: running.server._connected == 0)

    def test_the_default_path_is_filled_in(self, running: Running, sock: Path) -> None:
        lines = self._bridged(sock, call_line("where", {}), Path("/tree"))
        assert [text_of(line) for line in lines] == ["/tree"]

    def test_a_named_path_is_kept(self, running: Running, sock: Path) -> None:
        lines = self._bridged(sock, call_line("where", {"path": "/x"}), Path("/tree"))
        assert [text_of(line) for line in lines] == ["/x"]

    def test_without_a_default_path_nothing_is_filled_in(
        self, running: Running, sock: Path
    ) -> None:
        lines = self._bridged(sock, call_line("where", {}))
        assert [text_of(line) for line in lines] == ["None"]

    def test_a_server_that_hangs_up_ends_the_session(self, sock: Path) -> None:
        """The host is still there. The session ends, and the host asks again."""
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sock))
        listener.listen()

        def answer_once() -> None:
            connection, _address = listener.accept()
            with connection, connection.makefile("r") as reader:
                reader.readline()
                connection.sendall(b'{"jsonrpc": "2.0", "id": 1, "result": {}}\n')

        thread = threading.Thread(target=answer_once, daemon=True)
        thread.start()

        read_end, write_end = os.pipe()
        stdin = os.fdopen(read_end, "r")
        os.write(write_end, call_line("echo", {"x": "1"}).encode())
        connection = transport.connect(sock)
        assert connection is not None
        out = io.StringIO()
        try:
            transport.bridge(stdin, out, connection)
            assert json.loads(out.getvalue())["id"] == 1
        finally:
            os.close(write_end)
            stdin.close()
            thread.join(5)
            listener.close()

    def _server_that(
        self, sock: Path, behave
    ) -> tuple[socket.socket, threading.Thread]:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sock))
        listener.listen()

        def once() -> None:
            connection, _address = listener.accept()
            with connection:
                behave(connection)

        thread = threading.Thread(target=once, daemon=True)
        thread.start()
        return listener, thread

    def test_a_host_whose_input_is_gone(self, sock: Path) -> None:
        """Reading from the host fails. The server side ends the session."""
        listener, thread = self._server_that(sock, lambda connection: None)
        stdin = io.StringIO()
        stdin.close()
        connection = transport.connect(sock)
        assert connection is not None
        out = io.StringIO()
        try:
            transport.bridge(stdin, out, connection)
            assert out.getvalue() == ""
        finally:
            thread.join(5)
            listener.close()

    def test_a_server_that_resets_the_connection(self, sock: Path) -> None:
        """The server closes with the request unread. Reading from it fails."""

        def reset(connection: socket.socket) -> None:
            connection.recv(1)

        listener, thread = self._server_that(sock, reset)
        connection = transport.connect(sock)
        assert connection is not None
        out = io.StringIO()
        try:
            transport.bridge(
                io.StringIO(call_line("echo", {"x": "1"})), out, connection
            )
            assert out.getvalue() == ""
        finally:
            thread.join(5)
            listener.close()


class TestFillingThePath:
    """Verify only a tool call that names no path is touched."""

    TREE = Path("/tree")

    @pytest.mark.parametrize(
        "line",
        [
            "not json, though it says tools/call\n",
            '{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n',
            '["tools/call"]\n',
            '{"method": "tools/call"}\n',
            '{"method": "tools/call", "params": {"arguments": [1]}}\n',
            '{"method": "tools/call", "params": "arguments"}\n',
            '{"method": "ping", "params": {"note": "tools/call"}}\n',
        ],
    )
    def test_other_lines_pass_through(self, line: str) -> None:
        assert transport._with_path(line, self.TREE) == line

    def test_a_call_without_arguments(self) -> None:
        filled = json.loads(transport._with_path(call_line("where"), self.TREE))
        assert filled["params"]["arguments"] == {"path": "/tree"}

    def test_a_call_with_arguments_but_no_path(self) -> None:
        line = call_line("where", {"query": "q"})
        filled = json.loads(transport._with_path(line, self.TREE))
        assert filled["params"]["arguments"] == {"query": "q", "path": "/tree"}

    def test_a_call_with_a_path(self) -> None:
        line = call_line("where", {"path": "/x"})
        assert transport._with_path(line, self.TREE) == line
