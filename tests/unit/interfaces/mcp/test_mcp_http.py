"""Test MCP over HTTP against a real server on a free port."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from ish.interfaces.mcp import server as server_module
from ish.interfaces.mcp.protocol import Server, Tool
from ish.interfaces.mcp.transport import (
    MAX_BODY_BYTES,
    TOKEN_VARIABLE,
    HttpTransport,
    Transport,
)

TOKEN = "test-token-EXAMPLE"


class Running:
    """A server on a free port, served from a thread."""

    def __init__(self, tools: list[Tool]) -> None:
        self.transport = HttpTransport("127.0.0.1", 0, TOKEN)
        self.calls: list[str] = []
        server = Server("test", "0", tools)
        original = server.handle_line

        def handle(line: str):
            self.calls.append(line)
            return original(line)

        server.handle_line = handle
        self.thread = threading.Thread(
            target=self.transport.serve, args=(server,), daemon=True
        )
        self.thread.start()
        assert self.transport.ready.wait(5)
        assert self.transport.address is not None
        host, port = self.transport.address
        self.url = f"http://{host}:{port}"

    def post(
        self,
        message: dict | bytes,
        token: str | None = TOKEN,
        path: str = "/mcp",
    ) -> tuple[int, dict | None]:
        data = message if isinstance(message, bytes) else json.dumps(message).encode()
        request = urllib.request.Request(f"{self.url}{path}", data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def close(self) -> None:
        self.transport.stop()
        self.thread.join(5)


def _call(name: str, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }


@pytest.fixture()
def running() -> Iterator[Running]:
    made = Running([Tool("hello", "Say hello.", {"type": "object"}, lambda a: "hi")])
    yield made
    made.close()


class TestAuth:
    def test_the_right_token_runs_the_tool(self, running: Running) -> None:
        status, body = running.post(_call("hello"))
        assert status == 200
        assert body is not None
        assert body["result"]["content"][0]["text"] == "hi"

    @pytest.mark.parametrize("token", [None, "wrong", ""])
    def test_no_or_a_wrong_token_is_401_and_runs_nothing(
        self, running: Running, token
    ) -> None:
        assert running.post(_call("hello"), token=token)[0] == 401
        assert running.calls == []

    def test_no_token_configured_refuses_to_start(self) -> None:
        with pytest.raises(ValueError, match=TOKEN_VARIABLE):
            HttpTransport("127.0.0.1", 0, "")


class TestFraming:
    def test_a_notification_is_202_with_no_body(self, running: Running) -> None:
        status, body = running.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert (status, body) == (202, None)

    def test_another_path_is_404(self, running: Running) -> None:
        assert running.post(_call("hello"), path="/other")[0] == 404
        assert running.calls == []

    def test_a_get_is_405(self, running: Running) -> None:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{running.url}/mcp", timeout=10)
        assert caught.value.code == 405
        assert caught.value.headers["Allow"] == "POST"

    def test_a_body_too_large_is_413(self, running: Running) -> None:
        """Refuse on the declared length, before reading a byte of the body.

        Send the headers alone. A client still writing a large body when
        the refusal arrives can hit a broken pipe, which made a test that
        sent the whole body fail under load.
        """
        import http.client

        host, port = running.transport.address  # set once the server is ready
        connection = http.client.HTTPConnection(host, port, timeout=10)
        connection.putrequest("POST", "/mcp")
        connection.putheader("Authorization", f"Bearer {TOKEN}")
        connection.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
        connection.endheaders()
        assert connection.getresponse().status == 413
        connection.close()
        assert running.calls == []

    def test_a_malformed_message_is_a_json_rpc_error(self, running: Running) -> None:
        status, body = running.post(b"{not json")
        assert status == 200
        assert body is not None and body["error"]["code"] == -32700

    def test_it_is_a_transport(self) -> None:
        assert isinstance(HttpTransport("127.0.0.1", 0, TOKEN), Transport)

    def test_stop_before_serve_does_nothing(self) -> None:
        HttpTransport("127.0.0.1", 0, TOKEN).stop()


class TestConcurrency:
    def test_two_requests_run_at_the_same_time(self) -> None:
        """A slow answer for one client must not hold up another."""
        both = threading.Barrier(2, timeout=5)

        def meet(arguments) -> str:
            both.wait()
            return "met"

        made = Running([Tool("meet", "Wait for a second caller.", {}, meet)])
        try:
            results: list = []
            threads = [
                threading.Thread(
                    target=lambda n=n: results.append(made.post(_call("meet", n)))
                )
                for n in (1, 2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            texts = [body["result"]["content"][0]["text"] for _, body in results]
            assert texts == ["met", "met"]
        finally:
            made.close()


class TestEntryPoint:
    def test_http_without_a_token_exits_1(self, monkeypatch, tmp_path, caplog) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
        assert server_module.main(["--http", "127.0.0.1:0"]) == 1
        assert TOKEN_VARIABLE in caplog.text

    def test_http_with_a_token_serves_over_http(self, monkeypatch, tmp_path) -> None:
        served: list[tuple[str, int]] = []

        def serve(self, server) -> None:
            served.append((self._host, self._port))
            raise KeyboardInterrupt

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        monkeypatch.setattr(HttpTransport, "serve", serve)
        assert server_module.main(["--http", "0.0.0.0:8765"]) == 0
        assert served == [("0.0.0.0", 8765)]

    @pytest.mark.parametrize(
        ("text", "address"),
        [("127.0.0.1:8765", ("127.0.0.1", 8765)), ("8765", ("127.0.0.1", 8765))],
    )
    def test_an_address_is_read(self, text, address) -> None:
        assert server_module._address(text) == address

    def test_a_bad_address_names_the_option(self) -> None:
        with pytest.raises(ValueError, match="--http"):
            server_module._address("host:port")

    def test_a_bad_address_exits_1(self, monkeypatch, tmp_path, caplog) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        assert server_module.main(["--http", "nowhere"]) == 1
        assert "--http" in caplog.text
