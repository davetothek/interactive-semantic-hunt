"""Run `ish remote mcp` on stdio against a real server.

The stdio server answers `search_code` and `ask` from the tree of the
server, and the language model of this machine writes the answer.
"""

import io
import json

import pytest

from ish.interfaces.cli import run
from ish.settings import MCP_TOKEN_ENV_VAR
from tests.integration.cli.conftest import Served
from tests.integration.cli.test_cli_ask import FakeModel, _model

QUESTION = "what does the watchdog do"


def _call(number: int, name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": number,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _serve(monkeypatch: pytest.MonkeyPatch, argv: list[str], *messages: dict):
    """Run `ish` with *messages* on stdin. Return the exit code and the replies."""
    stdout = io.StringIO()
    lines = "".join(f"{json.dumps(message)}\n" for message in messages)
    monkeypatch.setattr("sys.stdin", io.StringIO(lines))
    monkeypatch.setattr("sys.stdout", stdout)
    code = run(argv)
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    return code, {reply["id"]: reply["result"] for reply in replies}


class TestRemoteMcp:
    def test_search_and_ask_come_from_the_server(
        self, served: Served, monkeypatch
    ) -> None:
        model = FakeModel()
        _model(monkeypatch, model)
        code, results = _serve(
            monkeypatch,
            ["remote", "mcp", "--url", served.url],
            _call(1, "search_code", {"query": "watchdog"}),
            _call(2, "ask", {"question": QUESTION}),
        )
        assert code == 0
        searched = results[1]["content"][0]["text"]
        assert searched.splitlines()[0] == "[1.00] docs/boot.md:1-3  document  Watchdog"
        answer = json.loads(results[2]["content"][0]["text"])
        assert answer["covered"] is True
        (citation,) = answer["citations"]
        assert citation["path"] == "docs/boot.md"
        assert citation["url"] == "https://docs.example.invalid/tree/docs/boot.md#L1-3"
        assert answer["server"]["host"] == served.host
        assert model.questions == [QUESTION]
        assert [name for name, _ in served.calls] == ["search_code", "sources"]

    def test_ish_remote_names_the_server(self, served: Served, monkeypatch) -> None:
        _model(monkeypatch, FakeModel())
        code, results = _serve(
            monkeypatch, ["remote", "mcp"], _call(1, "ask", {"question": QUESTION})
        )
        assert code == 0
        assert json.loads(results[1]["content"][0]["text"])["covered"] is True

    def test_a_call_with_a_path_reaches_no_server(
        self, served: Served, monkeypatch
    ) -> None:
        code, results = _serve(
            monkeypatch,
            ["remote", "mcp"],
            _call(1, "search_code", {"query": "watchdog", "path": "/srv/docs"}),
        )
        assert code == 0
        assert results[1]["isError"] is True
        assert served.calls == []

    def test_no_token_stops_the_server_before_a_request(
        self, served: Served, monkeypatch, caplog
    ) -> None:
        monkeypatch.delenv(MCP_TOKEN_ENV_VAR)
        code, results = _serve(
            monkeypatch, ["remote", "mcp"], _call(1, "search_code", {"query": "x"})
        )
        assert (code, results) == (1, {})
        assert f"Set {MCP_TOKEN_ENV_VAR} to the token of {served.url}." in caplog.text
        assert served.calls == []
