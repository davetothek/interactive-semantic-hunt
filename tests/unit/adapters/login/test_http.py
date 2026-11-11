"""Test the sign-in of this machine to a server, over a real sign-in path."""

import json
import threading
from collections.abc import Iterator

import pytest

from ish.adapters._logins import Saved, TokenFile
from ish.adapters.login import HttpLogin
from ish.adapters.login.http import login_url
from ish.application.ports.login import Login, LoginError, SignedIn
from ish.interfaces.mcp.protocol import Server
from ish.interfaces.mcp.transport import HttpTransport
from tests.unit.interfaces.mcp.test_mcp_http import ADMIT, PASSWORD, Desk


@pytest.fixture()
def served() -> Iterator[tuple[str, Desk]]:
    desk = Desk()
    transport = HttpTransport("127.0.0.1", 0, ADMIT, desk)
    thread = threading.Thread(
        target=transport.serve, args=(Server("t", "0", []),), daemon=True
    )
    thread.start()
    assert transport.ready.wait(5)
    assert transport.address is not None
    host, port = transport.address
    yield f"http://{host}:{port}/mcp", desk
    transport.stop()
    thread.join(5)


def _login(url: str, tmp_path) -> tuple[HttpLogin, TokenFile]:
    tokens = TokenFile(tmp_path / "logins.json")
    return HttpLogin(url, tokens, timeout=10), tokens


class TestLoginUrl:
    @pytest.mark.parametrize(
        ("url", "login"),
        [
            ("https://docs.example.invalid/mcp", "https://docs.example.invalid/login"),
            (
                "https://docs.example.invalid/ish/mcp",
                "https://docs.example.invalid/ish/login",
            ),
            ("http://127.0.0.1:8765/mcp", "http://127.0.0.1:8765/login"),
            (
                "https://docs.example.invalid/mcp?x=1",
                "https://docs.example.invalid/login",
            ),
        ],
    )
    def test_the_sign_in_path_is_beside_mcp(self, url: str, login: str) -> None:
        assert login_url(url) == login

    def test_a_url_that_does_not_end_in_mcp_is_refused(self) -> None:
        with pytest.raises(LoginError) as refused:
            login_url("https://docs.example.invalid/ish")
        assert str(refused.value) == (
            "The URL of the server ends in /mcp. Give https://docs.example.invalid/ish/mcp?"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://docs.example.invalid/mcp",
            "docs.example.invalid/mcp",
            "https://reader:hunter2-EXAMPLE@docs.example.invalid/mcp",
        ],
    )
    def test_another_url_is_refused_and_not_shown(self, url: str) -> None:
        with pytest.raises(LoginError) as refused:
            login_url(url)
        assert "hunter2-EXAMPLE" not in str(refused.value)
        assert str(refused.value).startswith("The URL of the server is not")


class TestPasswordProblem:
    @pytest.mark.parametrize(
        "url",
        [
            "https://docs.example.invalid/mcp",
            "http://127.0.0.1:8765/mcp",
            "http://[::1]:8765/mcp",
            "http://localhost:8765/mcp",
        ],
    )
    def test_https_or_this_machine_may_get_a_password(self, url: str, tmp_path) -> None:
        assert _login(url, tmp_path)[0].password_problem() == ""

    @pytest.mark.parametrize(
        "url", ["http://docs.example.invalid/mcp", "http://10.0.0.7:8765/mcp"]
    )
    def test_plain_http_to_another_machine_gets_no_password(
        self, url: str, tmp_path
    ) -> None:
        login, tokens = _login(url, tmp_path)
        problem = login.password_problem()
        assert problem.startswith("ish sends no password to")
        assert "Use the https URL of the server." in problem
        with pytest.raises(LoginError, match="ish sends no password"):
            login.sign_in("directory", "reader", PASSWORD)
        assert tokens.get(url) is None


class TestHttpLogin:
    def test_it_is_a_login(self, tmp_path) -> None:
        assert isinstance(
            _login("https://docs.example.invalid/mcp", tmp_path)[0], Login
        )

    def test_the_methods_come_with_no_password(self, served, tmp_path) -> None:
        url, desk = served
        assert _login(url, tmp_path)[0].methods() == ["directory"]
        assert desk.asked == []

    def test_a_sign_in_keeps_the_token_for_the_url(self, served, tmp_path) -> None:
        url, desk = served
        login, tokens = _login(url, tmp_path)
        assert login.sign_in("directory", "reader", PASSWORD) == SignedIn(
            "reader", 2_000_000_000.0
        )
        assert tokens.get(url) == Saved("login-token-EXAMPLE", "reader", 2e9)
        assert desk.asked == [("directory", "reader")]
        assert PASSWORD not in (tmp_path / "logins.json").read_text()

    def test_a_refusal_gives_the_reason_of_the_server(self, served, tmp_path) -> None:
        url, _ = served
        login, tokens = _login(url, tmp_path)
        with pytest.raises(LoginError) as refused:
            login.sign_in("directory", "reader", "wrong-EXAMPLE")
        assert (
            str(refused.value) == "The directory refused the user name or the password."
        )
        assert tokens.get(url) is None

    def test_a_directory_that_is_down_is_not_a_wrong_password(
        self, served, tmp_path
    ) -> None:
        url, _ = served
        with pytest.raises(LoginError, match="The directory cannot be reached."):
            _login(url, tmp_path)[0].sign_in("directory", "reader", "down-EXAMPLE")

    def test_sign_out_deletes_the_token(self, served, tmp_path) -> None:
        url, _ = served
        login, tokens = _login(url, tmp_path)
        login.sign_in("directory", "reader", PASSWORD)
        assert login.sign_out() is True
        assert tokens.get(url) is None
        assert login.sign_out() is False


class TestFailures:
    def test_a_server_with_no_sign_in_says_so(self, tmp_path) -> None:
        transport = HttpTransport("127.0.0.1", 0, ADMIT)
        thread = threading.Thread(
            target=transport.serve, args=(Server("t", "0", []),), daemon=True
        )
        thread.start()
        try:
            assert transport.ready.wait(5)
            assert transport.address is not None
            host, port = transport.address
            login, _ = _login(f"http://{host}:{port}/mcp", tmp_path)
            with pytest.raises(LoginError) as refused:
                login.sign_in("directory", "reader", PASSWORD)
        finally:
            transport.stop()
            thread.join(5)
        assert "has no sign-in" in str(refused.value)

    def test_a_server_nothing_reaches(self, tmp_path) -> None:
        with pytest.raises(LoginError, match="Cannot reach 127.0.0.1:9"):
            _login("http://127.0.0.1:9/mcp", tmp_path)[0].methods()


class _Reply:
    """A fake response of urllib."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class TestReplies:
    """A reply the client cannot use is one line that names the server."""

    URL = "https://docs.example.invalid/mcp"

    def _replying(self, monkeypatch, body: bytes) -> None:
        import urllib.request

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda request, timeout: _Reply(body)
        )

    @pytest.mark.parametrize("body", [b"not json", b"[1]"])
    def test_a_reply_that_is_not_json(self, body: bytes, monkeypatch, tmp_path) -> None:
        self._replying(monkeypatch, body)
        with pytest.raises(LoginError, match="did not answer with JSON"):
            _login(self.URL, tmp_path)[0].methods()

    def test_a_reply_with_no_list_of_methods(self, monkeypatch, tmp_path) -> None:
        self._replying(monkeypatch, b'{"methods": "directory"}')
        with pytest.raises(LoginError, match="did not list its sign-in methods"):
            _login(self.URL, tmp_path)[0].methods()

    def test_a_reply_with_no_token(self, monkeypatch, tmp_path) -> None:
        self._replying(monkeypatch, b'{"user": "reader"}')
        with pytest.raises(LoginError, match="sent no login token"):
            _login(self.URL, tmp_path)[0].sign_in("directory", "reader", PASSWORD)

    def test_a_refusal_with_no_reason_names_the_status(
        self, monkeypatch, tmp_path
    ) -> None:
        import io
        import urllib.error
        import urllib.request

        def refuse(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"<html>")
            )

        monkeypatch.setattr(urllib.request, "urlopen", refuse)
        with pytest.raises(LoginError) as refused:
            _login(self.URL, tmp_path)[0].sign_in("directory", "reader", PASSWORD)
        assert str(refused.value) == "docs.example.invalid answered HTTP 502."

    def test_the_password_goes_in_the_body_and_never_in_the_url(
        self, monkeypatch, tmp_path
    ) -> None:
        import urllib.request

        sent: list = []

        def record(request, timeout):
            sent.append(request)
            return _Reply(b'{"token": "t", "user": "reader", "expires": 1}')

        monkeypatch.setattr(urllib.request, "urlopen", record)
        _login(self.URL, tmp_path)[0].sign_in("directory", "reader", PASSWORD)
        (request,) = sent
        assert request.full_url == "https://docs.example.invalid/login"
        assert request.get_method() == "POST"
        assert json.loads(request.data)["password"] == PASSWORD
        assert PASSWORD not in request.full_url
        assert not [v for v in request.header_items() if PASSWORD in str(v)]

    def test_https_checks_the_certificate(self, monkeypatch, tmp_path) -> None:
        import urllib.request

        seen: list[dict] = []

        def record(request, **kwargs):
            seen.append(kwargs)
            return _Reply(b'{"methods": []}')

        monkeypatch.setattr(urllib.request, "urlopen", record)
        _login(self.URL, tmp_path)[0].methods()
        # No context: urllib checks the certificate with the system store.
        assert seen == [{"timeout": 10}]

    def test_a_token_file_that_cannot_be_written(self, monkeypatch, tmp_path) -> None:
        self._replying(monkeypatch, b'{"token": "t", "user": "reader", "expires": 1}')
        blocked = tmp_path / "file"
        blocked.write_text("")
        login = HttpLogin(self.URL, TokenFile(blocked / "logins.json"), timeout=10)
        with pytest.raises(LoginError, match="Cannot keep the login token"):
            login.sign_in("directory", "reader", PASSWORD)

    def test_a_token_file_that_cannot_be_read_at_sign_out(self, tmp_path) -> None:
        path = tmp_path / "logins.json"
        path.write_text("not json")
        path.chmod(0o600)
        login = HttpLogin(self.URL, TokenFile(path), timeout=10)
        with pytest.raises(LoginError, match="Cannot delete the login token"):
            login.sign_out()
