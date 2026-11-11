"""Run `ish --ask --remote` against a real MCP server on a free port."""

import json
from collections.abc import Iterator

import pytest

from ish import bootstrap
from ish.interfaces.cli import main as cli
from ish.interfaces.cli.main import main
from ish.interfaces.mcp.protocol import Tool
from ish.settings import MCP_TOKEN_ENV_VAR
from tests.unit.interfaces.mcp.test_mcp_http import TOKEN, Running

MARKDOWN = "# what does the watchdog do\n\nIt resets the chip.[^1]\n"


class Server:
    """An ask tool that records each call and answers what the test set."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls: list[dict] = []
        self.reply: dict | str = {
            "covered": True,
            "answer": "It resets the chip.",
            "markdown": MARKDOWN,
        }

    def ask(self, arguments: dict) -> str:
        self.calls.append(dict(arguments))
        if isinstance(self.reply, str):
            return self.reply
        return json.dumps(self.reply)


@pytest.fixture(scope="module")
def shared() -> Iterator[tuple[Server, str]]:
    # One server for the module. Stopping one waits out its poll interval.
    made = Server()
    running = Running([Tool("ask", "Answer.", {"type": "object"}, made.ask)])
    yield made, f"{running.url}/mcp"
    running.close()


@pytest.fixture()
def server(
    shared: tuple[Server, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[Server, str]:
    made, url = shared
    made.reset()
    monkeypatch.setenv(MCP_TOKEN_ENV_VAR, TOKEN)

    # A remote answer reads no local index and builds no local model.
    def local(*_args, **_kwargs):
        raise AssertionError("a remote answer touched the local tree")

    monkeypatch.setattr(bootstrap, "build_embedder", local)
    monkeypatch.setattr(bootstrap, "build_language_model", local)
    return made, url


QUESTION = "what does the watchdog do"


class TestRemoteAsk:
    def test_the_answer_prints_the_markdown_of_the_server(
        self, server: tuple[Server, str], capsys
    ) -> None:
        made, url = server
        assert main(["--ask", QUESTION, "--remote", url]) == 0
        assert capsys.readouterr().out == MARKDOWN + "\n"
        assert made.calls == [{"question": QUESTION, "brief": False}]

    def test_the_flag_gets_no_warning_while_no_command_replaces_it(
        self, server: tuple[Server, str], caplog
    ) -> None:
        _made, url = server
        assert main(["--ask", QUESTION, "--remote", url]) == 0
        assert "goes away" not in caplog.text

    def test_stderr_says_what_the_server_spent(
        self, server: tuple[Server, str], capsys
    ) -> None:
        made, url = server
        made.reply = {
            **made.reply,
            "model": "m",
            "input_tokens": 10,
            "output_tokens": 2,
            "timings": {"searching": 0.25, "writing": 1.5},
            "seconds": 1.75,
        }
        main(["--remote", url, "--ask", QUESTION])
        lines = capsys.readouterr().err.rstrip().splitlines()
        assert any(line.startswith("search ") and "0.2 s" in line for line in lines)
        answer = next(line for line in lines if line.startswith("answer "))
        assert answer.split() == ["answer", "1.5", "s", "m", "10", "2"]
        assert any(line.endswith("1.8 s on the server") for line in lines)

    def test_a_server_that_sends_no_timings_shows_the_wait(
        self, server: tuple[Server, str], capsys
    ) -> None:
        _made, url = server
        main(["--remote", url, "--ask", QUESTION])
        lines = capsys.readouterr().err.rstrip().splitlines()
        total = next(line for line in lines if line.startswith("total "))
        assert total.endswith(" s") and "on the server" not in total

    def test_the_last_line_names_both_versions(
        self, server: tuple[Server, str], capsys
    ) -> None:
        from ish.interfaces.cli.args import installed_version

        _made, url = server
        main(["--remote", url, "--ask", QUESTION])
        last = capsys.readouterr().err.rstrip().splitlines()[-1]
        assert last.startswith(f"ish {installed_version()} · server ")

    def test_brief_prints_the_short_answer(
        self, server: tuple[Server, str], capsys
    ) -> None:
        made, url = server
        assert main(["--brief", QUESTION, "--remote", url]) == 0
        assert capsys.readouterr().out == "It resets the chip.\n"
        assert made.calls[0]["brief"] is True

    def test_an_uncovered_question_exits_two(
        self, server: tuple[Server, str], capsys
    ) -> None:
        made, url = server
        made.reply = {"covered": False, "answer": "", "markdown": "# q\n"}
        assert main(["--ask", QUESTION, "--remote", url]) == 2
        assert capsys.readouterr().out == "# q\n\n"

    def test_the_filters_travel_with_the_question(
        self, server: tuple[Server, str]
    ) -> None:
        made, url = server
        argv = ["--ask", QUESTION, "--remote", url, "--lang", "asciidoc"]
        argv += ["--under", "docs/requirements", "--type", "doc"]
        assert main(argv) == 0
        (call,) = made.calls
        assert (call["lang"], call["under"], call["type"]) == (
            ["asciidoc"],
            "docs/requirements",
            ["doc"],
        )

    def test_ask_turns_travel_when_given_on_the_command_line(
        self, server: tuple[Server, str], monkeypatch
    ) -> None:
        made, url = server
        assert main(["--ask", QUESTION, "--remote", url, "--ask-turns", "2"]) == 0
        # A value from the environment or a config file stays here.
        monkeypatch.setenv("ISH_ASK_TURNS", "3")
        assert main(["--ask", QUESTION, "--remote", url]) == 0
        given, configured = made.calls
        assert given["ask_turns"] == 2
        assert "ask_turns" not in configured

    def test_the_busy_line_names_the_host(
        self, server: tuple[Server, str], monkeypatch
    ) -> None:
        _, url = server
        shown: list[str] = []

        class Recorder:
            def show(self, text: str) -> None:
                shown.append(text)

            def stop(self) -> None:
                pass

        monkeypatch.setattr(cli, "Ticker", Recorder)
        assert main(["--ask", QUESTION, "--remote", url]) == 0
        assert shown == [f"Asking {url.removeprefix('http://').removesuffix('/mcp')}"]


class TestRemoteFailures:
    """Each failure is one line on stderr and exit 1, with stdout empty."""

    def test_no_token(self, server, monkeypatch, capsys, caplog) -> None:
        _, url = server
        monkeypatch.delenv(MCP_TOKEN_ENV_VAR)
        assert main(["--ask", QUESTION, "--remote", url]) == 1
        assert MCP_TOKEN_ENV_VAR in caplog.text
        assert capsys.readouterr().out == ""

    def test_a_tool_the_server_refuses(self, server, capsys, caplog) -> None:
        made, url = server
        made.reply = "not json"
        assert main(["--ask", QUESTION, "--remote", url]) == 1
        assert "did not answer the question with JSON" in caplog.text
        assert capsys.readouterr().out == ""

    def test_a_server_too_old_to_send_markdown(self, server, capsys, caplog) -> None:
        made, url = server
        made.reply = {"covered": True, "answer": "It resets the chip."}
        assert main(["--ask", QUESTION, "--remote", url]) == 1
        assert "Update ish on the server" in caplog.text
        assert capsys.readouterr().out == ""

    def test_an_error_the_tool_reports(self, monkeypatch, capsys, caplog) -> None:
        def refuse(arguments: dict) -> str:
            raise ValueError("No language model is allowed on this server.")

        running = Running([Tool("ask", "Answer.", {"type": "object"}, refuse)])
        monkeypatch.setenv(MCP_TOKEN_ENV_VAR, TOKEN)
        try:
            code = main(["--ask", QUESTION, "--remote", f"{running.url}/mcp"])
        finally:
            running.close()
        assert code == 1
        assert "No language model is allowed" in caplog.text
        assert capsys.readouterr().out == ""

    def test_ctrl_c_exits_130(self, server, monkeypatch, capsys) -> None:
        _, url = server
        from ish.interfaces.mcp.client import RemoteClient

        def interrupted(self, tool, arguments):
            raise KeyboardInterrupt

        monkeypatch.setattr(RemoteClient, "call", interrupted)
        assert main(["--ask", QUESTION, "--remote", url]) == 130
        assert "interrupted" in capsys.readouterr().err
