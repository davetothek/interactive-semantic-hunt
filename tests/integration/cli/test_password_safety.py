"""One test for each rule under Password safety in the spec.

Each test is named for its rule, and fails when the rule is broken. The
server signs users in against a stand-in for an LDAP directory, through
the real LDAP adapter. No test reaches a real directory.
"""

import json
import logging
import ssl
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ish import bootstrap
from ish.adapters._logins import TokenFile
from ish.adapters.login import HttpLogin
from ish.application.access import MAX_FAILURES
from ish.application.ports.login import LoginError
from ish.interfaces.cli import run
from ish.interfaces.mcp import server as server_module
from ish.interfaces.mcp.protocol import Server
from ish.interfaces.mcp.transport import HttpTransport
from ish.settings import MCP_TOKEN_ENV_VAR, REMOTE_ENV_VAR, Settings, option_names
from tests.integration.cli.test_cli_ask import FakeEmbedder
from tests.unit.adapters.authenticator import fake_ldap
from tests.unit.adapters.authenticator.fake_ldap import Directory

STATIC = "test-token-EXAMPLE"
USER = "reader"
PASSWORD = "hunter2-EXAMPLE"
TEMPLATE = "uid={user},ou=people,dc=example,dc=invalid"
READER = "uid=reader,ou=people,dc=example,dc=invalid"


@dataclass
class Site:
    url: str
    index: Path
    logins: Path
    directory: Directory


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[Site]:
    tree = tmp_path_factory.mktemp("tree")
    (tree / "docs").mkdir()
    (tree / "docs" / "boot.md").write_text(
        "# Watchdog\n\nThe watchdog resets the board.\n"
    )
    index = tmp_path_factory.mktemp("idx")
    directory = Directory(users={READER: PASSWORD})
    settings = replace(
        Settings(),
        no_cache=True,
        git=False,
        cache_dir=str(index),
        auth=("token", "ldap"),
        ldap_url="ldaps://ldap.example.invalid",
        ldap_user_dn=TEMPLATE,
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bootstrap, "build_embedder", lambda s: FakeEmbedder())
        fake_ldap.install(patch, directory)
        patch.setenv(MCP_TOKEN_ENV_VAR, STATIC)
        patch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("data")))
        logins = bootstrap.login_file()
        access = bootstrap.build_access(settings)
        tools = server_module.IshTools(settings, tree)
        transport = HttpTransport("127.0.0.1", 0, access.admit, access)
        served = Server("ish", "0", tools.tools(over_http=True))
        thread = threading.Thread(target=transport.serve, args=(served,), daemon=True)
        thread.start()
        assert transport.ready.wait(5)
        assert transport.address is not None
        host, port = transport.address
        tools.search({"query": "watchdog"})
    yield Site(f"http://{host}:{port}/mcp", index, logins, directory)
    transport.stop()
    thread.join(5)
    tools.close()


@pytest.fixture()
def client(site: Site, tmp_path, monkeypatch) -> Site:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(REMOTE_ENV_VAR, site.url)
    monkeypatch.delenv(MCP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD)
    site.logins.unlink(missing_ok=True)
    site.directory.binds.clear()
    return site


def _login(user: str = USER) -> int:
    return run(["remote", "login", "--user", user])


def _saved_token() -> str:
    (entry,) = json.loads(bootstrap.token_file().read_text()).values()
    return entry["token"]


class TestPasswordSafety:
    def test_no_password_goes_over_plain_http_to_another_machine(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        asked: list[str] = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "getpass.getpass", lambda prompt: asked.append(prompt) or PASSWORD
        )
        login = HttpLogin("http://docs.example.invalid/mcp", TokenFile(tmp_path / "t"))
        monkeypatch.setattr(bootstrap, "build_login", lambda settings: _Listed(login))
        code = run(["remote", "login", "--url", "http://docs.example.invalid/mcp"])
        assert code == 1
        assert asked == []
        (error,) = [
            r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert error.startswith(
            "ish sends no password to docs.example.invalid over plain http"
        )
        assert "anyone on the network could read it" in error

    def test_https_checks_the_certificate_and_no_option_turns_it_off(
        self, tmp_path
    ) -> None:
        with _https_with_a_certificate_nobody_signed(tmp_path) as url:
            login = HttpLogin(url, TokenFile(tmp_path / "logins.json"), timeout=10)
            with pytest.raises(LoginError, match="CERTIFICATE_VERIFY_FAILED"):
                login.methods()
            from ish.adapters.chunk_source._client import RemoteClient
            from ish.application.ports.chunk_source import ChunkSourceError

            client = RemoteClient(url, STATIC, MCP_TOKEN_ENV_VAR, timeout=10)
            with pytest.raises(ChunkSourceError, match="CERTIFICATE_VERIFY_FAILED"):
                client.call("search_code", {"query": "x"})
        words = ("verify", "insecure", "cert", "ssl", "skip")
        assert not [name for name in option_names() if any(w in name for w in words)]

    @pytest.mark.parametrize("listen", ["0.0.0.0:8765", "10.0.0.7:8765"])
    def test_ldap_on_an_address_that_is_not_loopback_does_not_start(
        self, listen: str, tmp_path, monkeypatch, caplog
    ) -> None:
        fake_ldap.install(monkeypatch, Directory())
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(MCP_TOKEN_ENV_VAR, STATIC)
        monkeypatch.setenv("ISH_AUTH", "ldap")
        monkeypatch.setenv("ISH_LDAP_URL", "ldaps://ldap.example.invalid")
        monkeypatch.setenv("ISH_LDAP_USER_DN", TEMPLATE)
        monkeypatch.setenv("ISH_CACHE_DIR", str(tmp_path / "idx"))

        def serve(self, server):
            raise AssertionError("the server started")

        monkeypatch.setattr(HttpTransport, "serve", serve)
        assert server_module.main(["--http", listen]) == 1
        assert "listens on this machine only" in caplog.messages[-1]

    def test_an_empty_password_is_refused_and_the_directory_is_not_asked(
        self, client: Site, monkeypatch, caplog
    ) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "")
        assert _login() == 1
        assert [
            r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
        ] == ["Give a password."]
        assert client.directory.binds == []

    def test_the_user_name_is_escaped_before_it_reaches_the_directory(
        self, client: Site
    ) -> None:
        assert _login("reader,ou=admins") == 1
        assert client.directory.binds == [
            (r"uid=reader\,ou\=admins,ou=people,dc=example,dc=invalid", PASSWORD)
        ]

    def test_the_password_goes_in_the_body_and_never_in_a_url(
        self, client: Site, monkeypatch
    ) -> None:
        from ish.adapters.login import http as login_http

        sent: list = []
        original = login_http.open_without_redirects

        def record(request, timeout):
            sent.append(request)
            return original(request, timeout)

        monkeypatch.setattr(login_http, "open_without_redirects", record)
        assert _login() == 0
        (post,) = [r for r in sent if r.get_method() == "POST"]
        assert json.loads(post.data)["password"] == PASSWORD
        assert all(PASSWORD not in r.full_url for r in sent)

    def test_no_password_token_or_authorization_header_reaches_a_log(
        self, client: Site, monkeypatch, capsys, caplog
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="ish"):
            monkeypatch.setattr("getpass.getpass", lambda prompt: "wrong-EXAMPLE")
            assert run(["remote", "login", "--user", USER, "-vv"]) == 1
            monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD)
            assert run(["remote", "login", "--user", USER, "-vv"]) == 0
            assert run(["remote", "search", "watchdog", "-vv"]) == 0
            client.logins.write_text("")
            assert run(["remote", "search", "watchdog", "-vv"]) == 1
            monkeypatch.setenv(MCP_TOKEN_ENV_VAR, "wrong-token-EXAMPLE")
            assert run(["remote", "search", "watchdog", "-vv"]) == 1
        shown = capsys.readouterr()
        everything = caplog.text + shown.err + shown.out
        # The log is not empty, so the checks below read what it wrote.
        assert "POST /login" in everything
        assert "refused your login" in everything
        for secret in (
            PASSWORD,
            "wrong-EXAMPLE",
            _saved_token(),
            STATIC,
            "wrong-token-EXAMPLE",
        ):
            assert secret not in everything
        assert "Authorization" not in everything
        assert "Bearer" not in everything

    def test_no_password_is_stored(self, client: Site, tmp_path) -> None:
        import os

        assert _login() == 0
        stored = [
            client.index,
            client.logins.parent,
            bootstrap.token_file().parent,
            tmp_path,
        ]
        # Every directory where ish keeps a file, on either side.
        for variable in (
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR",
        ):
            stored.append(Path(os.environ[variable]))
        for root in stored:
            for path in root.rglob("*"):
                if path.is_file():
                    assert PASSWORD.encode() not in path.read_bytes(), path

    def test_the_server_stores_a_hash_of_each_login_token_and_never_the_token(
        self, client: Site
    ) -> None:
        import hashlib

        assert _login() == 0
        token = _saved_token()
        kept = client.logins.read_text()
        assert token not in kept
        (line,) = kept.splitlines()
        assert json.loads(line)["digest"] == hashlib.sha256(token.encode()).hexdigest()

    def test_repeated_failures_stop_more_attempts_and_the_directory_is_not_asked(
        self, client: Site, monkeypatch, caplog
    ) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "wrong-EXAMPLE")
        for _ in range(MAX_FAILURES):
            assert _login("limited") == 1
        asked = len(client.directory.binds)
        monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD)
        assert _login("limited") == 1
        assert len(client.directory.binds) == asked
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert errors[-1] == (
            "Too many failed sign-ins for limited. Try again in 15 minutes."
        )

    def test_plain_ldap_without_starttls_starts_with_one_warning(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        fake_ldap.install(monkeypatch, Directory())
        settings = replace(
            Settings(),
            cache_dir=str(tmp_path),
            auth=("ldap",),
            ldap_url="ldap://ldap.example.invalid",
            ldap_user_dn=TEMPLATE,
        )
        with caplog.at_level(logging.WARNING):
            access = bootstrap.build_access(settings)
        assert access.methods() == ["ldap"]
        assert caplog.messages == [
            "The directory at ldap.example.invalid gets each password in plain "
            "text. Use an ldaps URL, or set ldap_starttls = true."
        ]
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            bootstrap.build_access(replace(settings, ldap_starttls=True))
            bootstrap.build_access(
                replace(settings, ldap_url="ldaps://ldap.example.invalid")
            )
        assert caplog.messages == []


class _Listed:
    """A login whose server lists one method, with no request."""

    def __init__(self, login: HttpLogin) -> None:
        self._login = login
        self.host = login.host

    def methods(self) -> list[str]:
        return ["ldap"]

    def password_problem(self) -> str:
        return self._login.password_problem()

    def sign_in(self, method: str, user: str, password: str):
        return self._login.sign_in(method, user, password)

    def sign_out(self) -> bool:
        return self._login.sign_out()


class _Methods(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b'{"methods": []}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, format, *args) -> None:
        return None


class _https_with_a_certificate_nobody_signed:
    """Serve https on a free port with a certificate that signs itself."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def __enter__(self) -> str:
        key, cert = self._directory / "key.pem", self._directory / "cert.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self._server = HTTPServer(("127.0.0.1", 0), _Methods)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"https://{host}:{port}/mcp"

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)
