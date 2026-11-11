"""Test the line of `ish remote`: its commands, its options, and its errors."""

import logging

import pytest

from ish.interfaces.cli import run
from ish.interfaces.cli.remote import OPTIONS, build_parser
from ish.settings import CONFIG_FILENAME, REMOTE_ENV_VAR

# A server nothing listens on. A line that is refused never reaches for it.
NOWHERE = "http://127.0.0.1:9/mcp"

USE = "Use: ish remote search QUERY, or ish remote ask QUESTION"
NO_SERVER = "No server is set. Give --url URL, or set ISH_REMOTE."


@pytest.fixture()
def nowhere(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REMOTE_ENV_VAR, NOWHERE)
    monkeypatch.chdir(tmp_path)


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


class TestNoServer:
    @pytest.mark.parametrize(
        "line", [[], ["search", "watchdog"], ["ask", "what does the watchdog do"]]
    )
    def test_the_error_shows_the_option_and_the_variable(
        self, line: list[str], tmp_path, monkeypatch, caplog
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert run(["remote", *line]) == 1
        assert _errors(caplog) == [NO_SERVER]


class TestAUrlWithAPassword:
    """No line of a run shows a password that the URL of the server holds."""

    PASSWORD = "hunter2-EXAMPLE"

    @pytest.mark.parametrize("detail", [[], ["-vv"]])
    @pytest.mark.parametrize("command", [["search", "watchdog"], ["ask", "why"]])
    def test_the_run_stops_and_shows_no_password(
        self, command: list[str], detail: list[str], tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        url = f"https://reader:{self.PASSWORD}@docs.example.invalid/mcp"
        assert run(["remote", *command, "--url", url, *detail]) == 1
        shown = capsys.readouterr()
        assert shown.out == ""
        assert "holds a user name or a password" in shown.err
        assert self.PASSWORD not in shown.err

    def test_a_password_from_the_environment_stays_out_of_the_log(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        monkeypatch.chdir(tmp_path)
        url = f"https://reader:{self.PASSWORD}@docs.example.invalid/mcp"
        monkeypatch.setenv(REMOTE_ENV_VAR, url)
        assert run(["remote", "search", "watchdog", "-vv"]) == 1
        assert self.PASSWORD not in caplog.text


class TestTheCommand:
    def test_the_bare_command_names_its_commands(self, nowhere, caplog) -> None:
        assert run(["remote"]) == 1
        assert _errors(caplog) == [f"ish remote needs a command. {USE}"]

    def test_a_query_with_no_command_shows_ish_remote_search(
        self, nowhere, caplog
    ) -> None:
        assert run(["remote", "watchdog"]) == 1
        assert _errors(caplog) == [f"'watchdog' is not a command of ish remote. {USE}"]

    def test_a_word_that_is_no_command_comes_before_the_server(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        # The line is wrong with a server or with none, so say that first.
        monkeypatch.chdir(tmp_path)
        assert run(["remote", "watchdog"]) == 1
        assert _errors(caplog) == [f"'watchdog' is not a command of ish remote. {USE}"]

    @pytest.mark.parametrize(
        ("command", "text"), [("search", "query"), ("ask", "question")]
    )
    def test_a_command_needs_its_text(
        self, command: str, text: str, nowhere, caplog
    ) -> None:
        assert run(["remote", command]) == 1
        assert run(["remote", command, " "]) == 1
        assert _errors(caplog) == [f"ish remote {command} needs a {text}."] * 2

    @pytest.mark.parametrize(
        ("command", "text"), [("search", "query"), ("ask", "question")]
    )
    def test_a_path_is_refused(
        self, command: str, text: str, nowhere, tmp_path, caplog
    ) -> None:
        assert run(["remote", command, "watchdog", str(tmp_path)]) == 1
        assert _errors(caplog) == [
            f"ish remote {command} takes one {text} and no path. "
            "The server searches its own tree. Use --under to narrow a search."
        ]

    def test_brief_is_for_an_answer(self, nowhere, caplog) -> None:
        assert run(["remote", "search", "watchdog", "--brief"]) == 1
        assert _errors(caplog) == ["--brief is an option of ish remote ask."]

    def test_a_config_file_that_does_not_parse(self, nowhere, tmp_path, capsys) -> None:
        (tmp_path / CONFIG_FILENAME).write_text("remote = [\n")
        assert run(["remote", "search", "watchdog"]) == 1
        assert capsys.readouterr().err.startswith("ish: ")


class TestTheOptions:
    def test_the_help_shows_both_commands(self, capsys) -> None:
        with pytest.raises(SystemExit):
            run(["remote", "--help"])
        text = capsys.readouterr().out
        assert text.startswith("usage: ish remote search QUERY [options]\n")
        assert "ish remote ask QUESTION [--brief] [options]" in text

    def test_url_names_the_server(self) -> None:
        args = build_parser().parse_intermixed_args(["search", "q", "--url", NOWHERE])
        assert args.remote == NOWHERE

    def test_the_name_of_the_bare_command_is_not_an_option(self, capsys) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_intermixed_args(["search", "q", "--remote", NOWHERE])
        assert "--remote" in capsys.readouterr().err

    @pytest.mark.parametrize("option", ["--no-cache", "--embedder", "--refresh"])
    def test_an_option_for_a_tree_of_this_machine_is_refused(
        self, option: str, capsys
    ) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_intermixed_args(["search", "q", option])
        assert "unrecognized arguments" in capsys.readouterr().err

    def test_every_option_is_one_of_the_list(self) -> None:
        args = build_parser().parse_intermixed_args(["search", "q"])
        own = {"command", "words", "brief", "config"}
        assert set(vars(args)) == set(OPTIONS) | own

    def test_lang_takes_a_name_this_machine_does_not_know(self) -> None:
        args = build_parser().parse_intermixed_args(["search", "q", "--lang", "ada"])
        assert args.lang == ["ada"]
