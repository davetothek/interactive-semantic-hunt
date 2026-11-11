"""Test MCP over HTTP against a real server on a free port."""

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from ish.adapters.authenticator.token import StaticToken
from ish.interfaces.mcp import server as server_module
from ish.interfaces.mcp.protocol import Server, Tool
from ish.interfaces.mcp.transport import (
    MAX_BODY_BYTES,
    TOKEN_VARIABLE,
    HttpTransport,
    Transport,
)

TOKEN = "test-token-EXAMPLE"

# The check a server runs on each request, with the static token alone.
ADMIT = StaticToken(TOKEN, TOKEN_VARIABLE).admit


class Running:
    """A server on a free port, served from a thread."""

    def __init__(self, tools: list[Tool]) -> None:
        self.transport = HttpTransport("127.0.0.1", 0, ADMIT)
        self.calls: list[str] = []
        server = Server("test", "0", tools)
        original = server.handle_line

        def handle(line: str, who: str = ""):
            self.calls.append(line)
            return original(line, who)

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

    @pytest.mark.parametrize("header", [f"Basic {TOKEN}", f"bearer {TOKEN}", TOKEN])
    def test_the_token_counts_only_as_a_bearer_token(
        self, running: Running, header: str
    ) -> None:
        request = urllib.request.Request(
            f"{running.url}/mcp", data=json.dumps(_call("hello")).encode()
        )
        request.add_header("Authorization", header)
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=10)
        assert refused.value.code == 401
        assert running.calls == []

    def test_the_transport_asks_with_the_token_alone(self) -> None:
        asked: list[str] = []

        def admit(bearer: str):
            asked.append(bearer)
            return None

        transport = HttpTransport("127.0.0.1", 0, admit)
        thread = threading.Thread(target=transport.serve, args=(Server("t", "0", []),))
        thread.start()
        try:
            assert transport.ready.wait(5)
            assert transport.address is not None
            host, port = transport.address
            request = urllib.request.Request(
                f"http://{host}:{port}/mcp", data=b"{}", method="POST"
            )
            request.add_header("Authorization", f"Bearer {TOKEN}")
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(request, timeout=10)
        finally:
            transport.stop()
            thread.join(5)
        assert asked == [TOKEN]


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
        assert isinstance(HttpTransport("127.0.0.1", 0, ADMIT), Transport)

    def test_stop_before_serve_does_nothing(self) -> None:
        HttpTransport("127.0.0.1", 0, ADMIT).stop()


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

    def test_a_server_over_http_offers_the_sources_tool(
        self, monkeypatch, tmp_path
    ) -> None:
        offered: list[str] = []

        def serve(self, server) -> None:
            reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            offered.extend(tool["name"] for tool in reply["result"]["tools"])

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        monkeypatch.setattr(HttpTransport, "serve", serve)
        assert server_module.main(["--http", "127.0.0.1:8765"]) == 0
        assert "sources" in offered
        assert "ask" not in offered

    def test_a_server_over_http_shows_who_calls_at_every_verbosity(
        self, monkeypatch, tmp_path
    ) -> None:
        audit = logging.getLogger("ish.audit")
        monkeypatch.setattr(audit, "level", logging.NOTSET)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        monkeypatch.setattr(HttpTransport, "serve", lambda self, server: None)
        assert server_module.main(["--http", "127.0.0.1:8765"]) == 0
        assert audit.level == logging.INFO

    @pytest.mark.parametrize(
        "listen", ["127.0.0.1:8765", "8765", "localhost:8765", "[::1]:8765"]
    )
    def test_a_server_that_signs_users_in_listens_on_this_machine(
        self, listen: str, monkeypatch, tmp_path
    ) -> None:
        self._with_a_sign_in(monkeypatch, tmp_path)
        monkeypatch.setattr(HttpTransport, "serve", lambda self, server: None)
        assert server_module.main(["--http", listen]) == 0

    @pytest.mark.parametrize("listen", ["0.0.0.0:8765", "10.0.0.7:8765", "docs:8765"])
    def test_a_server_that_signs_users_in_does_not_listen_on_a_network(
        self, listen: str, monkeypatch, tmp_path, caplog
    ) -> None:
        self._with_a_sign_in(monkeypatch, tmp_path)
        assert server_module.main(["--http", listen]) == 1
        assert caplog.messages[-1] == (
            "A server that signs users in with directory listens on this machine "
            "only, behind a reverse proxy that holds the certificate. "
            "Give --http 127.0.0.1:PORT."
        )

    def test_a_server_with_the_static_token_alone_may_listen_on_a_network(
        self, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        monkeypatch.setattr(HttpTransport, "serve", lambda self, server: None)
        assert server_module.main(["--http", "0.0.0.0:8765"]) == 0

    def _with_a_sign_in(self, monkeypatch, tmp_path) -> None:
        from ish.adapters import authenticator
        from ish.application.ports.authenticator import Identity

        class Directory:
            @classmethod
            def configured(cls, options):
                return cls()

            def sign_in(self, user, password):
                return Identity(user, "directory")

        monkeypatch.setitem(
            authenticator.AUTHENTICATORS, "directory", Directory.configured
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        monkeypatch.setenv("ISH_AUTH", "token,directory")
        monkeypatch.setenv("ISH_CACHE_DIR", str(tmp_path / "idx"))

    def test_ish_mcp_has_no_option_for_a_server(self, capsys) -> None:
        # `ish remote mcp` reads a server. `ish mcp` is the local tree.
        with pytest.raises(SystemExit):
            server_module.main(["--remote", "https://docs.example.invalid/mcp"])
        assert "unrecognized arguments: --remote" in capsys.readouterr().err

    def test_a_server_with_no_model_and_no_api_key_starts(
        self, monkeypatch, tmp_path
    ) -> None:
        from ish import bootstrap

        def refuse(*_args, **_kwargs):
            raise AssertionError("a server over HTTP must not build a model")

        offered: list[str] = []

        def serve(self, server) -> None:
            reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            offered.extend(tool["name"] for tool in reply["result"]["tools"])

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
        monkeypatch.setattr(bootstrap, "build_language_model", refuse)
        monkeypatch.setattr(bootstrap, "build_reranker", refuse)
        monkeypatch.setattr(HttpTransport, "serve", serve)
        assert server_module.main(["--http", "127.0.0.1:8765"]) == 0
        assert "sources" in offered

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


class TestSourcesOverHttp:
    """A client on another machine reads the text of a chunk and a term."""

    class Embedder:
        model_name = "fake"

        def embed_documents(self, texts):
            return [[float(len(text)), 1.0] for text in texts]

        def embed_query(self, text):
            return [float(len(text)), 1.0]

    @pytest.fixture()
    def served(self, tmp_path, tmp_path_factory, monkeypatch) -> Iterator[Running]:
        from dataclasses import replace

        from ish import bootstrap
        from ish.settings import Settings

        (tmp_path / "exposure.md").write_text(
            "# Exposure\n\nThe Auto Exposure (AE) block runs before the gain.\n"
        )
        monkeypatch.setattr(bootstrap, "build_embedder", lambda s: self.Embedder())
        settings = replace(
            Settings(),
            no_cache=True,
            git=False,
            cache_dir=str(tmp_path_factory.mktemp("idx")),
        )
        tools = server_module.IshTools(settings, tmp_path)
        made = Running(tools.tools(over_http=True))
        yield made
        made.close()
        tools.close()

    def test_the_reply_holds_the_text_of_a_chunk_and_a_term(
        self, served: Running
    ) -> None:
        from ish.adapters.chunk_source._client import RemoteClient

        client = RemoteClient(f"{served.url}/mcp", TOKEN, "ISH_MCP_TOKEN", timeout=10)
        reply = json.loads(client.call("sources", {"query": "exposure", "limit": 5}))
        (source,) = reply["sources"]
        assert source["path"] == "exposure.md"
        assert "The Auto Exposure (AE) block runs before the gain." in source["text"]
        (term,) = reply["terms"]
        assert (term["term"], term["meaning"]) == ("AE", "Auto Exposure")
        assert term["source"]["path"] == "exposure.md"

    def test_a_call_without_the_token_reads_nothing(self, served: Running) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "sources", "arguments": {"query": "exposure"}},
        }
        assert served.post(message, token=None) == (401, None)
        assert served.calls == []


class TestNoAskOverHttp:
    """A server over HTTP answers no question. Its clients answer."""

    def test_an_old_client_that_asks_hears_the_tool_does_not_exist(
        self, tmp_path, monkeypatch
    ) -> None:
        from dataclasses import replace

        from ish import bootstrap
        from ish.settings import Settings

        def refuse(*_args, **_kwargs):
            raise AssertionError("a server over HTTP must not build a model")

        monkeypatch.setattr(bootstrap, "build_language_model", refuse)
        monkeypatch.setattr(bootstrap, "build_reranker", refuse)
        settings = replace(Settings(), no_cache=True, git=False)
        tools = server_module.IshTools(settings, tmp_path)
        made = Running(tools.tools(over_http=True))
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ask", "arguments": {"question": "why"}},
        }
        try:
            status, reply = made.post(message)
        finally:
            made.close()
            tools.close()
        assert status == 200
        assert reply is not None
        assert reply["result"]["isError"] is True
        assert reply["result"]["content"][0]["text"] == "Unknown tool: ask"


PASSWORD = "hunter2-EXAMPLE"


class Desk:
    """A sign-in desk with one method, which records who asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def methods(self) -> list[str]:
        return ["directory"]

    def sign_in(self, method: str, user: str, password: str):
        from ish.application.access import Granted
        from ish.application.ports.authenticator import (
            SignInRefused,
            SignInUnavailable,
        )

        self.asked.append((method, user))
        if not user:
            raise ValueError("Give a user name.")
        if password == "down-EXAMPLE":
            raise SignInUnavailable("The directory cannot be reached.")
        if password == "often-EXAMPLE":
            from ish.application.ports.authenticator import SignInLimited

            raise SignInLimited("Too many failed sign-ins for reader.")
        if password != PASSWORD:
            raise SignInRefused("The directory refused the user name or the password.")
        return Granted("login-token-EXAMPLE", user, 2_000_000_000.0)


class TestSignInPath:
    """`/login` lists the methods, and gives a login token for a password."""

    @pytest.fixture()
    def desk(self) -> Iterator[tuple[str, Desk]]:
        made = Desk()
        transport = HttpTransport("127.0.0.1", 0, ADMIT, made)
        thread = threading.Thread(
            target=transport.serve, args=(Server("t", "0", []),), daemon=True
        )
        thread.start()
        assert transport.ready.wait(5)
        assert transport.address is not None
        host, port = transport.address
        yield f"http://{host}:{port}", made
        transport.stop()
        thread.join(5)

    def _send(self, url: str, body: bytes | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{url}/login", data=body, method="GET" if body is None else "POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _sign_in(self, url: str, **fields: object) -> tuple[int, dict]:
        asked = {"method": "directory", "user": "reader", "password": PASSWORD}
        return self._send(url, json.dumps(asked | fields).encode())

    def test_a_get_lists_the_methods_with_no_token(self, desk) -> None:
        url, _ = desk
        assert self._send(url) == (200, {"methods": ["directory"]})

    def test_a_password_the_desk_accepts_gives_a_token(self, desk) -> None:
        url, made = desk
        assert self._sign_in(url) == (
            200,
            {"token": "login-token-EXAMPLE", "user": "reader", "expires": 2e9},
        )
        assert made.asked == [("directory", "reader")]

    def test_a_refusal_is_401_with_its_reason(self, desk) -> None:
        url, _ = desk
        assert self._sign_in(url, password="wrong-EXAMPLE") == (
            401,
            {"error": "The directory refused the user name or the password."},
        )

    def test_a_directory_that_is_down_is_503(self, desk) -> None:
        url, _ = desk
        assert self._sign_in(url, password="down-EXAMPLE") == (
            503,
            {"error": "The directory cannot be reached."},
        )

    def test_a_user_over_the_limit_is_429(self, desk) -> None:
        url, _ = desk
        assert self._sign_in(url, password="often-EXAMPLE") == (
            429,
            {"error": "Too many failed sign-ins for reader."},
        )

    def test_a_request_the_desk_cannot_take_is_400(self, desk) -> None:
        url, _ = desk
        assert self._sign_in(url, user="") == (400, {"error": "Give a user name."})

    @pytest.mark.parametrize(
        "body",
        [
            b"not json",
            b"[1, 2]",
            b'{"method": "directory", "user": "reader"}',
            b'{"method": "directory", "user": "reader", "password": 7}',
        ],
    )
    def test_a_body_that_is_not_a_sign_in_is_400(self, body: bytes, desk) -> None:
        url, made = desk
        status, reply = self._send(url, body)
        assert status == 400
        assert reply["error"].startswith("Send a JSON object with the method")
        assert made.asked == []

    def test_a_body_too_large_is_413(self, desk) -> None:
        url, made = desk
        request = urllib.request.Request(f"{url}/login", data=b"{}", method="POST")
        request.add_header("Content-Length", str(MAX_BODY_BYTES + 1))
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=10)
        assert refused.value.code == 413
        assert made.asked == []

    def test_no_line_of_the_log_holds_the_password(self, desk, caplog) -> None:
        url, _ = desk
        with caplog.at_level(logging.DEBUG, logger="ish"):
            self._sign_in(url)
            self._sign_in(url, password="wrong-EXAMPLE")
        # The log is not empty, so the checks below read the lines it wrote.
        assert "POST /login" in caplog.text
        assert PASSWORD not in caplog.text
        assert "wrong-EXAMPLE" not in caplog.text
        assert "login-token-EXAMPLE" not in caplog.text
        assert "Signed reader in, until 2033-05-18 03:33 UTC" in caplog.text

    def test_a_server_with_no_desk_has_no_sign_in_path(self, running: Running) -> None:
        get = urllib.request.Request(f"{running.url}/login")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(get, timeout=10)
        assert refused.value.code == 405
        assert running.post(b"{}", path="/login")[0] == 404


class TestAudit:
    """A tool call over HTTP names who made it, and never what it asked."""

    def test_the_static_token_calls_a_tool(self, running: Running, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="ish.audit"):
            running.post(_call("hello"))
        assert [r.getMessage() for r in caplog.records if r.name == "ish.audit"] == [
            "token calls hello"
        ]
