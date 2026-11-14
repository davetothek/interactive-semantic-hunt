"""Run `ish --ask --remote URL`, the line `ish remote ask` replaces.

The flag does for one release what `ish remote ask` does: the server
returns chunks, and the model of this machine answers. It prints one
warning that names the command.
"""

import logging

import pytest

from ish.adapters.chunk_source._client import RemoteClient
from ish.interfaces.cli.args import installed_version
from ish.interfaces.cli.main import main
from ish.settings import MCP_TOKEN_ENV_VAR, REMOTE_ENV_VAR
from tests.integration.cli.conftest import Served
from tests.integration.cli.test_cli_ask import FakeModel, _model

QUESTION = "what does the watchdog do"
ANSWER = "The watchdog resets the board [^1]."


@pytest.fixture()
def flagged(served: Served, monkeypatch: pytest.MonkeyPatch) -> Served:
    """Leave the flag as the one thing that names the server."""
    monkeypatch.delenv(REMOTE_ENV_VAR)
    return served


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


class TestRemoteAsk:
    def test_the_model_of_this_machine_answers_from_the_server(
        self, flagged: Served, monkeypatch, capsys
    ) -> None:
        model = FakeModel()
        _model(monkeypatch, model)
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 0
        assert model.questions == [QUESTION]
        assert flagged.calls == [("sources", {"query": QUESTION, "limit": 10})]
        out = capsys.readouterr().out
        assert out.startswith(f"# {QUESTION}\n\n{ANSWER}\n\n## Sources")
        assert "(https://docs.example.invalid/tree/docs/boot.md#L1-3)" in out

    def test_the_flag_warns_once_and_names_the_command(
        self, flagged: Served, monkeypatch, caplog
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 0
        assert _warnings(caplog) == [
            "--remote goes away in a later release. "
            "Use: ish remote ask QUESTION --url URL"
        ]

    def test_the_warning_keeps_the_address_of_the_server_out_of_the_log(
        self, flagged: Served, monkeypatch, caplog
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 0
        assert flagged.host not in caplog.text

    def test_the_last_line_names_both_versions_and_the_server(
        self, flagged: Served, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        main(["--remote", flagged.url, "--ask", QUESTION])
        last = capsys.readouterr().err.rstrip().splitlines()[-1]
        assert last == f"ish {installed_version()} · server 0 at {flagged.host}"

    def test_brief_prints_the_short_answer(
        self, flagged: Served, monkeypatch, capsys, caplog
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert main(["--brief", QUESTION, "--remote", flagged.url]) == 0
        assert capsys.readouterr().out == "The watchdog resets the board.\n"
        assert _warnings(caplog) == [
            "--remote goes away in a later release. "
            "Use: ish remote ask QUESTION --brief --url URL"
        ]

    def test_an_uncovered_question_exits_two(
        self, flagged: Served, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel(covered=False))
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 2
        assert capsys.readouterr().out.startswith(f"# {QUESTION}\n")

    def test_the_filters_travel_with_the_question(
        self, flagged: Served, monkeypatch
    ) -> None:
        _model(monkeypatch, FakeModel())
        argv = ["--ask", QUESTION, "--remote", flagged.url, "--lang", "markdown"]
        argv += ["--under", "docs", "--type", "doc"]
        assert main(argv) == 0
        ((_name, call),) = flagged.calls
        assert (call["lang"], call["under"], call["type"]) == (
            ["markdown"],
            "docs",
            ["doc"],
        )

    def test_a_filter_of_this_machine_stays_here(
        self, flagged: Served, monkeypatch
    ) -> None:
        _model(monkeypatch, FakeModel())
        monkeypatch.setenv("ISH_UNDER", "src")
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 0
        ((_name, call),) = flagged.calls
        assert "under" not in call


class TestRemoteFailures:
    """Each failure is one line on stderr and exit 1, with stdout empty."""

    def test_no_language_model_reads_no_text_of_the_server(
        self, flagged: Served, capsys, caplog
    ) -> None:
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 1
        assert "Set language_model to answer questions" in caplog.text
        assert flagged.calls == []
        assert capsys.readouterr().out == ""

    def test_no_token(self, flagged: Served, monkeypatch, capsys, caplog) -> None:
        _model(monkeypatch, FakeModel())
        monkeypatch.delenv(MCP_TOKEN_ENV_VAR)
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 1
        assert MCP_TOKEN_ENV_VAR in caplog.text
        assert capsys.readouterr().out == ""

    def test_ctrl_c_exits_130(self, flagged: Served, monkeypatch, capsys) -> None:
        _model(monkeypatch, FakeModel())

        def interrupted(self, tool, arguments):
            raise KeyboardInterrupt

        monkeypatch.setattr(RemoteClient, "call", interrupted)
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 130
        assert "interrupted" in capsys.readouterr().err


class TestStreamedRemoteAnswer:
    def test_a_terminal_gets_what_a_pipe_gets(
        self, flagged: Served, monkeypatch, capsys
    ) -> None:
        import sys

        from tests.integration.cli.test_cli_ask import StreamingModel

        model = StreamingModel()
        _model(monkeypatch, model)
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 0
        piped = capsys.readouterr().out
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert main(["--ask", QUESTION, "--remote", flagged.url]) == 0
        assert model.streamed is True
        assert capsys.readouterr().out == piped
        assert "It resets the board." in piped
