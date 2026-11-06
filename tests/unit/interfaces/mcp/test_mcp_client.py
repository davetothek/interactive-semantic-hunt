"""Test the MCP client against a real HTTP server on a free port."""

import json
from collections.abc import Iterator

import pytest

from ish.interfaces.mcp.client import RemoteClient, RemoteError
from ish.interfaces.mcp.protocol import Tool
from ish.settings import MCP_TOKEN_ENV_VAR
from tests.unit.interfaces.mcp.test_mcp_http import TOKEN, Running


def _echo(arguments: dict) -> str:
    return json.dumps(arguments)


def _broken(arguments: dict) -> str:
    raise ValueError("The 'question' argument is required.")


@pytest.fixture()
def running() -> Iterator[Running]:
    made = Running(
        [
            Tool("echo", "Return the arguments.", {"type": "object"}, _echo),
            Tool("broken", "Fail.", {"type": "object"}, _broken),
        ]
    )
    yield made
    made.close()


def _client(running: Running, token: str = TOKEN) -> RemoteClient:
    return RemoteClient(f"{running.url}/mcp", token, timeout=5)


class TestCall:
    def test_a_call_returns_the_text_of_the_tool(self, running: Running) -> None:
        text = _client(running).call("echo", {"question": "q", "brief": True})
        assert json.loads(text) == {"question": "q", "brief": True}

    def test_the_session_opens_once_before_the_first_call(
        self, running: Running
    ) -> None:
        client = _client(running)
        client.call("echo", {})
        client.call("echo", {})
        methods = [json.loads(line)["method"] for line in running.calls]
        assert methods == [
            "initialize",
            "notifications/initialized",
            "tools/call",
            "tools/call",
        ]

    def test_a_failed_tool_raises_with_its_message(self, running: Running) -> None:
        with pytest.raises(RemoteError, match="question"):
            _client(running).call("broken", {})

    def test_an_unknown_tool_raises(self, running: Running) -> None:
        with pytest.raises(RemoteError, match="nope"):
            _client(running).call("nope", {})


class TestRefusal:
    @pytest.mark.parametrize(
        "url", ["ish.example.com/mcp", "ftp://host/mcp", "http://"]
    )
    def test_a_url_that_is_not_http_is_refused(self, url: str) -> None:
        with pytest.raises(RemoteError, match="not an http or https URL"):
            RemoteClient(url, TOKEN)

    def test_the_host_names_the_server(self, running: Running) -> None:
        assert _client(running).host == running.url.removeprefix("http://")

    def test_no_token_is_refused_before_any_request(self, running: Running) -> None:
        with pytest.raises(RemoteError, match=MCP_TOKEN_ENV_VAR):
            _client(running, token="")
        assert running.calls == []

    def test_a_wrong_token_names_the_variable(self, running: Running) -> None:
        with pytest.raises(
            RemoteError, match=f"refused the token in {MCP_TOKEN_ENV_VAR}"
        ):
            _client(running, token="wrong").call("echo", {})

    def test_another_status_names_the_code(self, running: Running) -> None:
        client = RemoteClient(f"{running.url}/other", TOKEN, timeout=5)
        with pytest.raises(RemoteError, match="HTTP 404"):
            client.call("echo", {})

    def test_a_server_that_is_not_there_cannot_be_reached(
        self, running: Running
    ) -> None:
        url = f"{running.url}/mcp"
        running.close()
        with pytest.raises(RemoteError, match="Cannot reach"):
            RemoteClient(url, TOKEN, timeout=5).call("echo", {})


class TestBadReplies:
    """A server that breaks the protocol gets a message, not a traceback."""

    def _replying(self, monkeypatch, body: bytes) -> RemoteClient:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> bytes:
                return body

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda request, timeout: Response()
        )
        return RemoteClient("https://example.invalid/mcp", TOKEN)

    def test_a_body_that_is_not_json(self, monkeypatch) -> None:
        with pytest.raises(RemoteError, match="did not answer with JSON"):
            self._replying(monkeypatch, b"<html>").call("echo", {})

    def test_an_empty_reply_to_a_request(self, monkeypatch) -> None:
        with pytest.raises(RemoteError, match="no reply to initialize"):
            self._replying(monkeypatch, b"").call("echo", {})

    def test_a_json_rpc_error(self, monkeypatch) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "bad"}})
        with pytest.raises(RemoteError, match="refused initialize: bad"):
            self._replying(monkeypatch, body.encode()).call("echo", {})
