"""A real ish server on a free port, for the commands that read one."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from ish import bootstrap
from ish.interfaces.mcp import server as server_module
from ish.settings import MCP_TOKEN_ENV_VAR, REMOTE_ENV_VAR, Settings
from tests.integration.cli.test_cli_ask import FakeEmbedder
from tests.unit.interfaces.mcp.test_mcp_http import TOKEN, Running

LINK_BASE = "https://docs.example.invalid/tree"


class Served:
    """The server of a test, and the tool calls it took."""

    def __init__(self, running: Running) -> None:
        self._running = running
        self.url = f"{running.url}/mcp"
        self.host = running.url.removeprefix("http://")

    def forget(self) -> None:
        """Drop the calls of the tests before this one."""
        del self._running.calls[:]

    def tool_names(self) -> list[str]:
        """Return the name of each tool the server lists."""
        _status, reply = self._running.post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert reply is not None
        return [tool["name"] for tool in reply["result"]["tools"]]

    @property
    def calls(self) -> list[tuple[str, dict]]:
        """Return each tool call as its name and its arguments."""
        messages = [json.loads(line) for line in self._running.calls]
        return [
            (message["params"]["name"], message["params"]["arguments"])
            for message in messages
            if message.get("method") == "tools/call"
        ]


@pytest.fixture(scope="module")
def running_server(tmp_path_factory) -> Iterator[Served]:
    # One server for the module. Stopping one waits out its poll interval.
    tree = tmp_path_factory.mktemp("tree")
    docs = tree / "docs"
    docs.mkdir()
    (docs / "boot.md").write_text("# Watchdog\n\nThe watchdog resets the board.\n")
    (docs / "other.md").write_text("# Colors\n\nThe sky is blue.\n")
    # No language model and no API key: the server answers no question.
    settings = replace(
        Settings(),
        no_cache=True,
        git=False,
        cache_dir=str(tmp_path_factory.mktemp("idx")),
        link_base=LINK_BASE,
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bootstrap, "build_embedder", lambda s: FakeEmbedder())
        tools = server_module.IshTools(settings, tree)
        made = Running(tools.tools(over_http=True))
        # Build the index now, so that no test of a client pays for it.
        made.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "search_code", "arguments": {"query": "watchdog"}},
            }
        )
    yield Served(made)
    made.close()
    tools.close()


@pytest.fixture()
def served(
    running_server: Served, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Served:
    """Name the server and its token, in a directory with no tree.

    A command that reads a server reads no index of this machine, so a
    run that builds an embedder here fails.
    """
    running_server.forget()
    monkeypatch.setenv(MCP_TOKEN_ENV_VAR, TOKEN)
    monkeypatch.setenv(REMOTE_ENV_VAR, running_server.url)
    monkeypatch.chdir(tmp_path)

    def local(*_args, **_kwargs):
        raise AssertionError("a command for a server read a tree of this machine")

    monkeypatch.setattr(bootstrap, "build_embedder", local)
    return running_server
