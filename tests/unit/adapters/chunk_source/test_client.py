"""Test the MCP client against a real HTTP server on a free port."""

import json
from collections.abc import Iterator

import pytest

from ish.adapters.chunk_source._client import RemoteClient
from ish.application.ports.chunk_source import ChunkSourceError
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
    return RemoteClient(f"{running.url}/mcp", token, MCP_TOKEN_ENV_VAR, timeout=5)


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
        with pytest.raises(ChunkSourceError, match="question"):
            _client(running).call("broken", {})

    def test_an_unknown_tool_raises(self, running: Running) -> None:
        with pytest.raises(ChunkSourceError, match="nope"):
            _client(running).call("nope", {})


class TestRefusal:
    @pytest.mark.parametrize(
        "url", ["ish.example.com/mcp", "ftp://host/mcp", "http://"]
    )
    def test_a_url_that_is_not_http_is_refused(self, url: str) -> None:
        with pytest.raises(ChunkSourceError, match="not an http or https URL"):
            RemoteClient(url, TOKEN, MCP_TOKEN_ENV_VAR)

    def test_the_host_names_the_server(self, running: Running) -> None:
        assert _client(running).host == running.url.removeprefix("http://")

    def test_an_at_sign_in_the_path_is_no_password(self) -> None:
        client = RemoteClient(
            "https://docs.example.invalid/@team/mcp", TOKEN, MCP_TOKEN_ENV_VAR
        )
        assert client.host == "docs.example.invalid"

    def test_no_token_is_refused_before_any_request(self, running: Running) -> None:
        with pytest.raises(ChunkSourceError, match=MCP_TOKEN_ENV_VAR):
            _client(running, token="")
        assert running.calls == []

    def test_a_wrong_token_names_the_variable(self, running: Running) -> None:
        with pytest.raises(
            ChunkSourceError, match=f"refused the token in {MCP_TOKEN_ENV_VAR}"
        ):
            _client(running, token="wrong").call("echo", {})

    def test_a_login_the_server_refuses_says_to_sign_in_again(
        self, running: Running
    ) -> None:
        client = RemoteClient(
            f"{running.url}/mcp", "wrong", MCP_TOKEN_ENV_VAR, timeout=5, login=True
        )
        with pytest.raises(ChunkSourceError) as refused:
            client.call("echo", {})
        assert str(refused.value) == (
            f"{running.url}/mcp refused your login. Run: ish remote login"
        )

    def test_another_status_names_the_code(self, running: Running) -> None:
        client = RemoteClient(
            f"{running.url}/other", TOKEN, MCP_TOKEN_ENV_VAR, timeout=5
        )
        with pytest.raises(ChunkSourceError, match="HTTP 404"):
            client.call("echo", {})

    def test_a_server_that_is_not_there_cannot_be_reached(
        self, running: Running
    ) -> None:
        url = f"{running.url}/mcp"
        running.close()
        with pytest.raises(ChunkSourceError, match="Cannot reach"):
            RemoteClient(url, TOKEN, MCP_TOKEN_ENV_VAR, timeout=5).call("echo", {})


class TestUrlWithAPassword:
    """The host and each error name the server, so its URL holds no secret.

    A URL such as ``https://user:password@host/mcp`` put the password in
    the line that names the server, and in every error of a call.
    """

    PASSWORD = "hunter2-EXAMPLE"

    @pytest.mark.parametrize(
        "url",
        [
            f"https://reader:{PASSWORD}@docs.example.invalid/mcp",
            f"https://{PASSWORD}@docs.example.invalid/mcp",
            f"ftp://reader:{PASSWORD}@docs.example.invalid/mcp",
            f"reader:{PASSWORD}@docs.example.invalid/mcp",
        ],
    )
    def test_the_url_is_refused_and_the_error_does_not_show_it(self, url: str) -> None:
        with pytest.raises(ChunkSourceError) as failure:
            RemoteClient(url, TOKEN, MCP_TOKEN_ENV_VAR)
        assert self.PASSWORD not in str(failure.value)
        assert "reader" not in str(failure.value)

    def test_the_error_says_where_the_token_goes(self) -> None:
        url = f"https://reader:{self.PASSWORD}@docs.example.invalid/mcp"
        with pytest.raises(ChunkSourceError) as failure:
            RemoteClient(url, TOKEN, MCP_TOKEN_ENV_VAR)
        assert str(failure.value) == (
            "The URL of the server holds a user name or a password. "
            f"Give the URL alone. The token goes in {MCP_TOKEN_ENV_VAR}."
        )

    def test_a_url_with_no_password_is_still_shown(self) -> None:
        with pytest.raises(ChunkSourceError) as failure:
            RemoteClient("ish.example.com/mcp", TOKEN, MCP_TOKEN_ENV_VAR)
        assert str(failure.value) == (
            "'ish.example.com/mcp' is not an http or https URL."
        )


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
            "ish.adapters.chunk_source._client.open_without_redirects",
            lambda request, timeout: Response(),
        )
        return RemoteClient("https://example.invalid/mcp", TOKEN, MCP_TOKEN_ENV_VAR)

    def test_a_body_that_is_not_json(self, monkeypatch) -> None:
        with pytest.raises(ChunkSourceError, match="did not answer with JSON"):
            self._replying(monkeypatch, b"<html>").call("echo", {})

    def test_an_empty_reply_to_a_request(self, monkeypatch) -> None:
        with pytest.raises(ChunkSourceError, match="no reply to initialize"):
            self._replying(monkeypatch, b"").call("echo", {})

    def test_a_json_rpc_error(self, monkeypatch) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "bad"}})
        with pytest.raises(ChunkSourceError, match="refused initialize: bad"):
            self._replying(monkeypatch, body.encode()).call("echo", {})


class TestNoRedirect:
    def test_a_redirect_sends_the_token_nowhere(self, monkeypatch) -> None:
        import io
        import urllib.error

        def moved(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 308, "Moved", {}, io.BytesIO(b"")
            )

        monkeypatch.setattr(
            "ish.adapters.chunk_source._client.open_without_redirects", moved
        )
        client = RemoteClient("https://example.invalid/mcp", TOKEN, MCP_TOKEN_ENV_VAR)
        with pytest.raises(ChunkSourceError) as refused:
            client.call("echo", {})
        assert str(refused.value) == (
            "https://example.invalid/mcp answered with a redirect. ish follows no "
            "redirect, so the token goes to no other address. Give the URL the "
            "server answers on."
        )
