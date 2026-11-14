"""Sign in to a real server with `ish remote login`, and read it with the token.

The server runs a stand-in for a directory: one user with one password.
No test reaches a real directory.
"""

import json
import logging
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ish import bootstrap
from ish.adapters import authenticator
from ish.application.ports.authenticator import Identity, SignInRefused
from ish.interfaces.cli import run
from ish.interfaces.mcp import server as server_module
from ish.interfaces.mcp.protocol import Server
from ish.interfaces.mcp.transport import HttpTransport
from ish.settings import MCP_TOKEN_ENV_VAR, REMOTE_ENV_VAR, Settings
from tests.integration.cli.test_cli_ask import FakeEmbedder

TOKEN = "test-token-EXAMPLE"
USER = "reader"
PASSWORD = "hunter2-EXAMPLE"
FIRST = "[1.00] docs/boot.md:1-3  document  Watchdog"


class Directory:
    """A stand-in for a directory: one user with one password."""

    @classmethod
    def configured(cls, options) -> "Directory":
        return cls()

    def sign_in(self, user: str, password: str) -> Identity:
        if (user, password) != (USER, PASSWORD):
            raise SignInRefused("The directory refused the user name or the password.")
        return Identity(user, "directory")


@dataclass
class Site:
    url: str
    logins: Path


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Iterator[Site]:
    tree = tmp_path_factory.mktemp("tree")
    (tree / "docs").mkdir()
    (tree / "docs" / "boot.md").write_text(
        "# Watchdog\n\nThe watchdog resets the board.\n"
    )
    index = tmp_path_factory.mktemp("idx")
    settings = replace(
        Settings(),
        no_cache=True,
        git=False,
        cache_dir=str(index),
        auth=("token", "directory"),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bootstrap, "build_embedder", lambda s: FakeEmbedder())
        patch.setitem(authenticator.AUTHENTICATORS, "directory", Directory.configured)
        patch.setenv(MCP_TOKEN_ENV_VAR, TOKEN)
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
        # Build the index now, through the static token.
        tools.search({"query": "watchdog"})
    yield Site(f"http://{host}:{port}/mcp", logins)
    transport.stop()
    thread.join(5)
    tools.close()


@pytest.fixture()
def client(site: Site, tmp_path, monkeypatch) -> Site:
    """Name the server, with no static token, in a directory with no tree."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(REMOTE_ENV_VAR, site.url)
    monkeypatch.delenv(MCP_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD)
    site.logins.unlink(missing_ok=True)
    return site


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


def _sign_in() -> None:
    assert run(["remote", "login", "--user", USER]) == 0


class TestSignIn:
    def test_a_sign_in_gives_a_token_that_opens_the_server(
        self, client: Site, capsys, caplog
    ) -> None:
        _sign_in()
        host = client.url.removeprefix("http://").removesuffix("/mcp")
        assert capsys.readouterr().err.startswith(
            f"Signed in to {host} as reader until "
        )
        with caplog.at_level(logging.INFO, logger="ish.audit"):
            assert run(["remote", "search", "watchdog"]) == 0
        assert capsys.readouterr().out.splitlines()[0] == FIRST
        audit = [r.getMessage() for r in caplog.records if r.name == "ish.audit"]
        assert audit == ["reader calls search_code"]

    def test_neither_side_keeps_the_password_and_the_server_keeps_no_token(
        self, client: Site
    ) -> None:
        _sign_in()
        tokens = bootstrap.token_file()
        assert stat.S_IMODE(tokens.stat().st_mode) == 0o600
        (kept,) = json.loads(tokens.read_text()).values()
        assert PASSWORD not in tokens.read_text()
        server_side = client.logins.read_text()
        assert stat.S_IMODE(client.logins.stat().st_mode) == 0o600
        assert PASSWORD not in server_side
        assert kept["token"] not in server_side
        (line,) = server_side.splitlines()
        assert json.loads(line)["user"] == USER

    def test_a_wrong_password_gives_no_token(
        self, client: Site, monkeypatch, caplog
    ) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "wrong-EXAMPLE")
        assert run(["remote", "login", "--user", USER]) == 1
        assert _errors(caplog) == [
            "The directory refused the user name or the password."
        ]
        assert not bootstrap.token_file().exists()
        assert "wrong-EXAMPLE" not in caplog.text


class TestTheEndOfALogin:
    REFUSED = "refused your login. Run: ish remote login"

    def test_the_operator_deletes_the_login(self, client: Site, caplog) -> None:
        _sign_in()
        client.logins.write_text("")
        assert run(["remote", "search", "watchdog"]) == 1
        assert _errors(caplog) == [f"{client.url} {self.REFUSED}"]

    def test_the_server_refuses_a_login_that_has_expired(
        self, client: Site, caplog
    ) -> None:
        _sign_in()
        (line,) = client.logins.read_text().splitlines()
        client.logins.write_text(json.dumps(json.loads(line) | {"expires": 1.0}) + "\n")
        assert run(["remote", "search", "watchdog"]) == 1
        assert _errors(caplog) == [f"{client.url} {self.REFUSED}"]

    def test_the_client_knows_its_login_has_ended(self, client: Site, caplog) -> None:
        _sign_in()
        tokens = bootstrap.token_file()
        kept = json.loads(tokens.read_text())
        kept[client.url]["expires"] = 1_700_000_000.0
        tokens.write_text(json.dumps(kept))
        assert run(["remote", "search", "watchdog"]) == 1
        assert _errors(caplog) == [
            f"Your login to {client.url} ended on 2023-11-14 22:13 UTC. "
            "Run: ish remote login"
        ]

    def test_logout_deletes_the_token(self, client: Site, caplog) -> None:
        _sign_in()
        assert run(["remote", "logout"]) == 0
        assert run(["remote", "search", "watchdog"]) == 1
        assert _errors(caplog) == [
            f"Set {MCP_TOKEN_ENV_VAR} to the token of {client.url}, "
            "or run: ish remote login"
        ]
