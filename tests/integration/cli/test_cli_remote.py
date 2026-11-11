"""Run `ish remote search` and `ish remote ask` against a real server."""

import logging

import pytest

from ish import bootstrap
from ish.application.ports.language_model import Passage, Reply
from ish.interfaces.cli import remote as program
from ish.interfaces.cli import run
from ish.interfaces.cli.args import installed_version
from ish.interfaces.mcp.protocol import Tool
from ish.settings import (
    CONFIG_FILENAME,
    MCP_TOKEN_ENV_VAR,
    REMOTE_ENV_VAR,
    Settings,
    user_config_path,
)
from tests.integration.cli.conftest import Served
from tests.integration.cli.test_cli_ask import FakeModel, _model
from tests.unit.interfaces.mcp.test_mcp_http import TOKEN, Running

QUESTION = "what does the watchdog do"
ANSWER = "The watchdog resets the board [^1]."

# A server nothing listens on. A run that reaches for it fails.
NOWHERE = "http://127.0.0.1:9/mcp"

FIRST = "[1.00] docs/boot.md:1-3  document  Watchdog"


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


def _user_config(text: str) -> None:
    """Write the config file of the user, which the test isolates."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _names_the_server(served: Served) -> str:
    return f"ish {installed_version()} · server 0 at {served.host}"


class Silent:
    """A model that fails the test when a command asks it."""

    model_name = "silent"

    def answer(self, question, sources):
        raise AssertionError("the command asked the model")


class TestSearch:
    def test_it_prints_the_lines_of_the_server(self, served: Served, capsys) -> None:
        assert run(["remote", "search", "watchdog"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == FIRST
        assert len(lines) == 2

    def test_stderr_names_the_server_that_answered(
        self, served: Served, capsys
    ) -> None:
        assert run(["remote", "search", "watchdog"]) == 0
        assert capsys.readouterr().err == f"{_names_the_server(served)}\n"

    def test_it_asks_with_the_limit_and_the_format_of_this_machine(
        self, served: Served
    ) -> None:
        assert run(["remote", "search", "watchdog"]) == 0
        arguments = {"query": "watchdog", "limit": Settings().limit, "format": "plain"}
        assert served.calls == [("search_code", arguments)]

    def test_the_limit_and_the_format_travel(self, served: Served, capsys) -> None:
        argv = ["remote", "search", "watchdog", "--limit", "1", "--format", "grep"]
        assert run(argv) == 0
        assert capsys.readouterr().out == "docs/boot.md:1:1:[1.00] document  Watchdog\n"

    def test_a_filter_on_the_line_travels(self, served: Served, capsys) -> None:
        argv = ["remote", "search", "watchdog", "--under", "docs", "--type", "doc"]
        assert run([*argv, "--lang", "markdown"]) == 0
        (_name, arguments) = served.calls[0]
        assert (arguments["lang"], arguments["under"], arguments["type"]) == (
            ["markdown"],
            "docs",
            ["doc"],
        )
        assert capsys.readouterr().out.splitlines()[0] == FIRST

    def test_a_language_this_machine_does_not_know_travels(
        self, served: Served, capsys
    ) -> None:
        # The server knows the languages of its tree. This machine has no list.
        assert run(["remote", "search", "watchdog", "--lang", "klingon"]) == 0
        assert served.calls[0][1]["lang"] == ["klingon"]
        assert capsys.readouterr().out == ""

    def test_a_filter_of_this_machine_stays_here(
        self, served: Served, tmp_path, monkeypatch
    ) -> None:
        # A filter in the environment or a config file is for a local tree.
        monkeypatch.setenv("ISH_UNDER", "src")
        (tmp_path / CONFIG_FILENAME).write_text('type = ["test"]\n')
        assert run(["remote", "search", "watchdog"]) == 0
        (_name, arguments) = served.calls[0]
        assert "under" not in arguments
        assert "type" not in arguments

    def test_nothing_found_prints_nothing(self, served: Served, capsys) -> None:
        assert run(["remote", "search", "watchdog", "--type", "test"]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == f"{_names_the_server(served)}\n"

    def test_an_option_may_stand_before_the_query(self, served: Served, capsys) -> None:
        assert run(["remote", "search", "--limit", "1", "watchdog"]) == 0
        assert capsys.readouterr().out == f"{FIRST}\n"

    def test_color_always_greys_the_line_about_the_server(
        self, served: Served, capsys
    ) -> None:
        assert run(["remote", "search", "watchdog", "--color", "always"]) == 0
        captured = capsys.readouterr()
        assert captured.err.startswith("\x1b[")
        assert "\x1b[" not in captured.out


class TestWhichServer:
    def test_the_url_option_wins_over_the_environment(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv(REMOTE_ENV_VAR, NOWHERE)
        assert run(["remote", "search", "watchdog", "--url", served.url]) == 0
        assert capsys.readouterr().out.splitlines()[0] == FIRST

    def test_the_environment_wins_over_the_user_config_file(
        self, served: Served, capsys
    ) -> None:
        _user_config(f'remote = "{NOWHERE}"\n')
        assert run(["remote", "search", "watchdog"]) == 0
        assert capsys.readouterr().out.splitlines()[0] == FIRST

    def test_the_user_config_file_names_the_server(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        monkeypatch.delenv(REMOTE_ENV_VAR)
        _user_config(f'remote = "{served.url}"\n')
        assert run(["remote", "search", "watchdog"]) == 0
        assert capsys.readouterr().out.splitlines()[0] == FIRST

    def test_a_named_config_file_names_the_server(
        self, served: Served, tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.delenv(REMOTE_ENV_VAR)
        named = tmp_path / "docs.toml"
        named.write_text(f'remote = "{served.url}"\n')
        assert run(["remote", "search", "watchdog", "--config", str(named)]) == 0
        assert capsys.readouterr().out.splitlines()[0] == FIRST


class TestAFileInATreeNamesNoServer:
    """A tree that names the server would choose where the token goes."""

    def test_the_file_is_named_and_no_request_is_sent(
        self, served: Served, tmp_path, monkeypatch, caplog
    ) -> None:
        monkeypatch.delenv(REMOTE_ENV_VAR)
        project = tmp_path / CONFIG_FILENAME
        project.write_text(f'remote = "{served.url}"\n')
        assert run(["remote", "search", "watchdog"]) == 1
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert warnings == [
            f"Ignore remote in {project}. A file in a tree does not name a server. "
            f"Give --url, set {REMOTE_ENV_VAR}, or set remote in {user_config_path()}."
        ]
        assert _errors(caplog) == [
            "No server is set. Give --url URL, or set ISH_REMOTE."
        ]
        assert served.calls == []

    def test_a_file_above_the_tree_names_no_server_either(
        self, served: Served, tmp_path, monkeypatch, caplog
    ) -> None:
        monkeypatch.delenv(REMOTE_ENV_VAR)
        (tmp_path / CONFIG_FILENAME).write_text(f'remote = "{served.url}"\n')
        below = tmp_path / "docs"
        below.mkdir()
        monkeypatch.chdir(below)
        assert run(["remote", "search", "watchdog"]) == 1
        assert served.calls == []

    def test_the_other_options_of_the_file_still_apply(
        self, served: Served, tmp_path, capsys
    ) -> None:
        (tmp_path / CONFIG_FILENAME).write_text(f'remote = "{NOWHERE}"\nlimit = 1\n')
        assert run(["remote", "search", "watchdog"]) == 0
        assert capsys.readouterr().out.splitlines() == [FIRST]


class TestAsk:
    def test_the_model_of_this_machine_answers_from_the_server(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        model = FakeModel()
        _model(monkeypatch, model)
        assert run(["remote", "ask", QUESTION]) == 0
        assert model.questions == [QUESTION]
        assert served.calls == [("sources", {"query": QUESTION, "limit": 10})]
        heading, answer, sources_heading, source = [
            block.strip() for block in capsys.readouterr().out.split("\n\n")
        ]
        assert heading == f"# {QUESTION}"
        assert answer == ANSWER
        assert sources_heading == "## Sources"
        link, about, where = source.split("\n")
        # The server gives the link and the type. No file of this machine does.
        assert link == (
            "[^1]: [boot.md:1-3](https://docs.example.invalid/tree/docs/boot.md#L1-3)"
        )
        assert about.strip() == "doc · Watchdog"
        assert where.strip() == "docs/"

    def test_a_server_with_no_model_and_no_api_key_serves_the_answer(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert "ask" not in served.tool_names()
        _model(monkeypatch, FakeModel())
        assert run(["remote", "ask", QUESTION]) == 0
        assert [name for name, _ in served.calls] == ["sources"]
        assert ANSWER in capsys.readouterr().out

    def test_stderr_ends_with_the_table_and_names_the_server(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert run(["remote", "ask", QUESTION]) == 0
        lines = capsys.readouterr().err.splitlines()
        assert lines[0] == ""
        assert lines[1].split() == ["stage", "time", "model", "in", "out"]
        assert any(line.startswith("answer ") for line in lines)
        assert lines[-2:] == ["", _names_the_server(served)]

    def test_brief_prints_the_short_answer_alone(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        kinds: list[bool] = []

        def build(settings, *, brief=False):
            kinds.append(brief)
            return FakeModel()

        monkeypatch.setattr(bootstrap, "build_language_model", build)
        assert run(["remote", "ask", QUESTION, "--brief"]) == 0
        assert capsys.readouterr().out == "The watchdog resets the board.\n"
        assert kinds == [True]

    def test_an_uncovered_question_exits_two(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel(covered=False))
        assert run(["remote", "ask", QUESTION]) == 2
        assert capsys.readouterr().out.startswith(f"# {QUESTION}\n")

    def test_a_search_that_finds_nothing_asks_no_model(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, Silent())
        assert run(["remote", "ask", QUESTION, "--type", "test"]) == 2
        assert [name for name, _arguments in served.calls] == ["sources"]
        assert ANSWER not in capsys.readouterr().out

    def test_a_filter_on_the_line_travels(self, served: Served, monkeypatch) -> None:
        _model(monkeypatch, FakeModel())
        argv = ["remote", "ask", QUESTION, "--under", "docs", "--type", "doc"]
        assert run([*argv, "--lang", "markdown"]) == 0
        assert served.calls == [
            (
                "sources",
                {
                    "query": QUESTION,
                    "limit": 10,
                    "lang": ["markdown"],
                    "under": "docs",
                    "type": ["doc"],
                },
            )
        ]

    def test_a_follow_up_search_goes_to_the_server(
        self, served: Served, monkeypatch
    ) -> None:
        notes: list[str] = []

        class Recorder:
            def show(self, text: str) -> None:
                pass

            def note(self, text: str) -> None:
                notes.append(text)

            def stop(self) -> None:
                pass

        class Agent:
            model_name = "agent"

            def step(self, question, sources, searched, turns_left):
                if not searched and turns_left:
                    return Reply(passages=(), covered=False, searches=("the sky",))
                return Reply(passages=(Passage("Found.", ("1",)),), covered=True)

        monkeypatch.setattr(program, "Ticker", Recorder)
        _model(monkeypatch, Agent())
        assert run(["remote", "ask", QUESTION, "--ask-turns", "2"]) == 0
        assert [arguments["query"] for _name, arguments in served.calls] == [
            QUESTION,
            "the sky",
        ]
        assert notes == ["Searching again: the sky"]

    def test_ctrl_c_exits_130_with_no_traceback(
        self, served: Served, monkeypatch, capsys
    ) -> None:
        class Interrupted:
            model_name = "interrupted"

            def answer(self, question, sources):
                raise KeyboardInterrupt

        _model(monkeypatch, Interrupted())
        assert run(["remote", "ask", QUESTION]) == 130
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.endswith("ish: interrupted\n")
        assert "Traceback" not in captured.err


class TestTheModelOfThisMachine:
    """The machine that asks decides which model reads the text of a server."""

    def test_no_language_model_refuses_before_any_request(
        self, served: Served, capsys, caplog
    ) -> None:
        assert run(["remote", "ask", QUESTION]) == 1
        assert _errors(caplog) == [
            "No language model is set. Set language_model to answer questions."
        ]
        assert served.calls == []
        assert capsys.readouterr().out == ""

    def test_a_model_the_allowlist_does_not_name_refuses_before_any_request(
        self, served: Served, capsys, caplog
    ) -> None:
        argv = ["remote", "ask", QUESTION, "--language-model", "claude-cli"]
        assert run(argv) == 1
        (error,) = _errors(caplog)
        assert error.startswith("Language model 'claude-cli' is not allowed.")
        assert "allowed_language_models" in error
        assert served.calls == []
        assert capsys.readouterr().out == ""


class TestFailures:
    """Each failure is one line on stderr and exit 1, with stdout empty."""

    @pytest.mark.parametrize("command", [["search", "watchdog"], ["ask", QUESTION]])
    def test_no_token(
        self, command: list[str], served: Served, monkeypatch, capsys, caplog
    ) -> None:
        _model(monkeypatch, Silent())
        monkeypatch.delenv(MCP_TOKEN_ENV_VAR)
        assert run(["remote", *command]) == 1
        assert _errors(caplog) == [
            f"Set {MCP_TOKEN_ENV_VAR} to the token of {served.url}."
        ]
        assert served.calls == []
        assert capsys.readouterr().out == ""

    def test_a_token_the_server_refuses(
        self, served: Served, monkeypatch, capsys, caplog
    ) -> None:
        monkeypatch.setenv(MCP_TOKEN_ENV_VAR, "wrong-token-EXAMPLE")
        assert run(["remote", "search", "watchdog"]) == 1
        assert _errors(caplog) == [
            f"{served.url} refused the token in {MCP_TOKEN_ENV_VAR}."
        ]
        assert "wrong-token-EXAMPLE" not in caplog.text
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("command", [["search", "watchdog"], ["ask", QUESTION]])
    def test_a_server_nothing_reaches(
        self, command: list[str], served: Served, monkeypatch, capsys, caplog
    ) -> None:
        _model(monkeypatch, Silent())
        assert run(["remote", *command, "--url", NOWHERE]) == 1
        (error,) = _errors(caplog)
        assert error.startswith(f"Cannot reach {NOWHERE}: ")
        assert capsys.readouterr().out == ""

    def test_a_server_with_no_sources_tool(self, monkeypatch, capsys, caplog) -> None:
        old = Running([Tool("hello", "Say hello.", {"type": "object"}, lambda a: "hi")])
        monkeypatch.setenv(MCP_TOKEN_ENV_VAR, TOKEN)
        _model(monkeypatch, Silent())
        try:
            code = run(["remote", "ask", QUESTION, "--url", f"{old.url}/mcp"])
        finally:
            old.close()
        assert code == 1
        (error,) = _errors(caplog)
        assert error.endswith("has no sources tool. Update ish on the server.")
        assert capsys.readouterr().out == ""

    def test_verbose_adds_the_traceback(self, served: Served, capsys) -> None:
        assert run(["remote", "search", "watchdog", "--url", NOWHERE, "-vv"]) == 1
        assert "Traceback" in capsys.readouterr().err
