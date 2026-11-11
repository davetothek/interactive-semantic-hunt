"""Test the line of `ish remote`: its commands, its options, and its errors."""

import io
import json
import logging

import pytest

from ish import bootstrap
from ish.application.ports.chunk_source import ChunkSourceError
from ish.interfaces.cli import run
from ish.interfaces.cli.remote import OPTIONS, build_parser
from ish.settings import CONFIG_FILENAME, REMOTE_ENV_VAR

# A server nothing listens on. A line that is refused never reaches for it.
NOWHERE = "http://127.0.0.1:9/mcp"

USE = (
    "Use: ish remote search QUERY, ish remote ask QUESTION, ish remote mcp, "
    "ish remote login, or ish remote logout"
)
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
        assert "ish remote mcp [options]" in text

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
        own = {"command", "words", "brief", "config", "method", "user"}
        assert set(vars(args)) == set(OPTIONS) | own

    def test_lang_takes_a_name_this_machine_does_not_know(self) -> None:
        args = build_parser().parse_intermixed_args(["search", "q", "--lang", "ada"])
        assert args.lang == ["ada"]


LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


class Served:
    """A chunk source that a test of `ish remote mcp` may build and not call."""

    host = "docs.example.invalid"
    version = "1.2.3"


class TestMcp:
    """`ish remote mcp` serves two tools on stdio for the tree of a server."""

    @pytest.fixture()
    def built(self, tmp_path, monkeypatch) -> list[str]:
        seen: list[str] = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            bootstrap,
            "build_chunk_source",
            lambda settings: seen.append(settings.remote) or Served(),
        )
        return seen

    def _serve(self, monkeypatch, argv: list[str], *lines: dict) -> tuple[int, list]:
        stdout = io.StringIO()
        text = "".join(f"{json.dumps(line)}\n" for line in lines)
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        monkeypatch.setattr("sys.stdout", stdout)
        code = run(argv)
        return code, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_url_names_the_server_and_two_tools_are_served(
        self, built: list[str], monkeypatch
    ) -> None:
        code, replies = self._serve(
            monkeypatch, ["remote", "mcp", "--url", NOWHERE], LIST
        )
        assert code == 0
        ((reply),) = replies
        names = [tool["name"] for tool in reply["result"]["tools"]]
        assert names == ["search_code", "ask"]
        assert built == [NOWHERE]

    def test_ish_remote_names_the_server(self, built: list[str], monkeypatch) -> None:
        monkeypatch.setenv(REMOTE_ENV_VAR, NOWHERE)
        assert self._serve(monkeypatch, ["remote", "mcp"])[0] == 0
        assert built == [NOWHERE]

    def test_no_server_shows_the_option_and_the_variable(
        self, built: list[str], caplog
    ) -> None:
        assert run(["remote", "mcp"]) == 1
        assert _errors(caplog) == [NO_SERVER]
        assert built == []

    @pytest.mark.parametrize(
        ("extra", "error"),
        [
            (
                ["watchdog"],
                "ish remote mcp takes no query. Each call of a tool brings its own.",
            ),
            (["--brief"], "--brief is an option of ish remote ask."),
            (
                ["--under", "docs"],
                "ish remote mcp takes no --lang, --under, or --type. "
                "Each call of a tool brings its own.",
            ),
            (
                ["--lang", "markdown"],
                "ish remote mcp takes no --lang, --under, or --type. "
                "Each call of a tool brings its own.",
            ),
            (
                ["--type", "doc"],
                "ish remote mcp takes no --lang, --under, or --type. "
                "Each call of a tool brings its own.",
            ),
        ],
    )
    def test_the_line_takes_no_text_and_no_filter(
        self, extra: list[str], error: str, built: list[str], caplog
    ) -> None:
        assert run(["remote", "mcp", "--url", NOWHERE, *extra]) == 1
        assert _errors(caplog) == [error]
        assert built == []

    def test_a_client_error_at_start_is_one_line(
        self, tmp_path, monkeypatch, capsys, caplog
    ) -> None:
        def refuse(settings):
            raise ChunkSourceError("Set ISH_MCP_TOKEN to the token of the server.")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(bootstrap, "build_chunk_source", refuse)
        assert run(["remote", "mcp", "--url", NOWHERE]) == 1
        assert _errors(caplog) == ["Set ISH_MCP_TOKEN to the token of the server."]
        assert capsys.readouterr().out == ""

    def test_ctrl_c_stops_the_server(self, built: list[str], monkeypatch) -> None:
        from ish.interfaces.mcp.protocol import Server

        def interrupted(self, stream_in, stream_out):
            raise KeyboardInterrupt

        monkeypatch.setattr(Server, "serve", interrupted)
        assert run(["remote", "mcp", "--url", NOWHERE]) == 0


PASSWORD = "hunter2-EXAMPLE"


class FakeLogin:
    """A server to sign in to, which records each sign-in."""

    host = "docs.example.invalid"

    def __init__(self, methods=("directory",), problem: str = "") -> None:
        self._methods = list(methods)
        self._problem = problem
        self.signed: list[tuple[str, str, str]] = []
        self.kept = True

    def methods(self) -> list[str]:
        return self._methods

    def password_problem(self) -> str:
        return self._problem

    def sign_in(self, method: str, user: str, password: str):
        from ish.application.ports.login import LoginError, SignedIn

        self.signed.append((method, user, password))
        if password != PASSWORD:
            raise LoginError("The directory refused the user name or the password.")
        return SignedIn(user, 2_000_000_000.0)

    def sign_out(self) -> bool:
        kept, self.kept = self.kept, False
        return kept


class TestLogin:
    """`ish remote login` asks for what it needs, and keeps no password."""

    @pytest.fixture()
    def server(self, nowhere, monkeypatch) -> FakeLogin:
        made = FakeLogin()
        monkeypatch.setattr(bootstrap, "build_login", lambda settings: made)
        monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD)
        return made

    def _answers(self, monkeypatch, *answers: str) -> list[str]:
        asked: list[str] = []
        left = list(answers)

        def answer(prompt: str) -> str:
            asked.append(prompt)
            return left.pop(0)

        monkeypatch.setattr("builtins.input", answer)
        return asked

    def test_one_method_and_a_user_need_only_the_password(
        self, server: FakeLogin, monkeypatch, capsys
    ) -> None:
        asked = self._answers(monkeypatch)
        assert run(["remote", "login", "--user", "reader"]) == 0
        assert server.signed == [("directory", "reader", PASSWORD)]
        assert asked == []
        assert capsys.readouterr().err == (
            "Signed in to docs.example.invalid as reader until 2033-05-18 03:33 UTC.\n"
        )

    def test_the_user_name_is_asked_for(self, server: FakeLogin, monkeypatch) -> None:
        asked = self._answers(monkeypatch, " reader ")
        assert run(["remote", "login"]) == 0
        assert asked == ["User name at docs.example.invalid: "]
        assert server.signed == [("directory", "reader", PASSWORD)]

    def test_the_password_prompt_names_the_user_and_the_server(
        self, server: FakeLogin, monkeypatch
    ) -> None:
        prompts: list[str] = []
        monkeypatch.setattr(
            "getpass.getpass", lambda prompt: prompts.append(prompt) or PASSWORD
        )
        assert run(["remote", "login", "--user", "reader"]) == 0
        assert prompts == ["Password of reader at docs.example.invalid: "]

    def test_method_selects_one_of_several_with_no_prompt(
        self, server: FakeLogin, monkeypatch
    ) -> None:
        server._methods = ["directory", "other"]
        asked = self._answers(monkeypatch)
        assert run(["remote", "login", "--method", "other", "--user", "reader"]) == 0
        assert server.signed == [("other", "reader", PASSWORD)]
        assert asked == []

    def test_several_methods_on_a_terminal_ask_which(
        self, server: FakeLogin, monkeypatch
    ) -> None:
        server._methods = ["directory", "other"]
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        asked = self._answers(monkeypatch, "other", "reader")
        assert run(["remote", "login"]) == 0
        assert asked[0] == "Sign-in method at docs.example.invalid (directory, other): "
        assert server.signed == [("other", "reader", PASSWORD)]

    def test_an_answer_that_is_no_method_stops(
        self, server: FakeLogin, monkeypatch, caplog
    ) -> None:
        server._methods = ["directory", "other"]
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        self._answers(monkeypatch, "magic")
        assert run(["remote", "login"]) == 1
        assert _errors(caplog) == [
            "'magic' is not a sign-in method of docs.example.invalid. "
            "It offers: directory, other."
        ]
        assert server.signed == []

    def test_several_methods_with_no_terminal_name_the_option(
        self, server: FakeLogin, monkeypatch, caplog
    ) -> None:
        server._methods = ["directory", "other"]
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        assert run(["remote", "login"]) == 1
        assert _errors(caplog) == [
            "docs.example.invalid offers several sign-in methods: directory, other. "
            "Give --method NAME."
        ]

    def test_a_method_the_server_does_not_offer(
        self, server: FakeLogin, caplog
    ) -> None:
        assert run(["remote", "login", "--method", "magic"]) == 1
        assert _errors(caplog) == [
            "docs.example.invalid offers no sign-in method 'magic'. "
            "It offers: directory."
        ]

    def test_a_server_with_no_sign_in_names_the_token(
        self, server: FakeLogin, caplog
    ) -> None:
        server._methods = []
        assert run(["remote", "login"]) == 1
        assert _errors(caplog) == [
            "docs.example.invalid offers no sign-in. Ask its operator for a token, "
            "and set ISH_MCP_TOKEN."
        ]

    def test_no_password_is_asked_for_a_server_that_may_get_none(
        self, server: FakeLogin, monkeypatch, caplog
    ) -> None:
        server._problem = (
            "ish sends no password to docs.example.invalid over plain http."
        )

        def refuse(prompt):
            raise AssertionError("the password was asked for")

        monkeypatch.setattr("getpass.getpass", refuse)
        assert run(["remote", "login", "--user", "reader"]) == 1
        assert _errors(caplog) == [server._problem]

    def test_ctrl_d_at_a_prompt_signs_nobody_in(
        self, server: FakeLogin, monkeypatch, capsys
    ) -> None:
        def closed(prompt):
            raise EOFError

        monkeypatch.setattr("getpass.getpass", closed)
        assert run(["remote", "login", "--user", "reader"]) == 130
        assert capsys.readouterr().err.endswith("ish: no sign-in\n")
        assert server.signed == []

    def test_a_refusal_is_one_line_that_holds_no_password(
        self, server: FakeLogin, monkeypatch, capsys, caplog
    ) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "wrong-EXAMPLE")
        assert run(["remote", "login", "--user", "reader", "-vv"]) == 1
        assert _errors(caplog) == [
            "The directory refused the user name or the password."
        ]
        shown = capsys.readouterr()
        assert "wrong-EXAMPLE" not in shown.err + shown.out + caplog.text

    def test_a_server_that_cannot_be_read(self, nowhere, monkeypatch, caplog) -> None:
        from ish.application.ports.login import LoginError

        def refuse(settings):
            raise LoginError("The URL of the server ends in /mcp.")

        monkeypatch.setattr(bootstrap, "build_login", refuse)
        assert run(["remote", "login"]) == 1
        assert _errors(caplog) == ["The URL of the server ends in /mcp."]


class TestLogout:
    @pytest.fixture()
    def server(self, nowhere, monkeypatch) -> FakeLogin:
        made = FakeLogin()
        monkeypatch.setattr(bootstrap, "build_login", lambda settings: made)
        return made

    def test_logout_deletes_the_token(self, server: FakeLogin, capsys) -> None:
        assert run(["remote", "logout"]) == 0
        assert capsys.readouterr().err == "Signed out of docs.example.invalid.\n"
        assert run(["remote", "logout"]) == 0
        assert capsys.readouterr().err == (
            "No login token of docs.example.invalid is kept here.\n"
        )

    def test_a_token_file_that_cannot_be_read(
        self, server: FakeLogin, monkeypatch, caplog
    ) -> None:
        from ish.application.ports.login import LoginError

        def broken():
            raise LoginError("Cannot delete the login token: no such file")

        monkeypatch.setattr(server, "sign_out", broken)
        assert run(["remote", "logout"]) == 1
        assert _errors(caplog) == ["Cannot delete the login token: no such file"]


class TestTheLineOfLoginAndLogout:
    @pytest.mark.parametrize(
        ("line", "error"),
        [
            (["login", "reader"], "ish remote login takes no query."),
            (["logout", "x"], "ish remote logout takes no query."),
            (
                ["login", "--under", "docs"],
                "ish remote login takes no --lang, --under, or --type.",
            ),
            (["logout", "--brief"], "--brief is an option of ish remote ask."),
            (
                ["logout", "--method", "x"],
                "--method and --user are options of ish remote login.",
            ),
            (
                ["mcp", "--user", "x"],
                "--method and --user are options of ish remote login.",
            ),
            (
                ["search", "watchdog", "--method", "x"],
                "--method and --user are options of ish remote login.",
            ),
        ],
    )
    def test_the_line_is_refused(
        self, line: list[str], error: str, nowhere, caplog
    ) -> None:
        assert run(["remote", *line]) == 1
        assert _errors(caplog) == [error]

    @pytest.mark.parametrize("command", ["login", "logout"])
    def test_no_server_shows_the_option_and_the_variable(
        self, command: str, tmp_path, monkeypatch, caplog
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert run(["remote", command]) == 1
        assert _errors(caplog) == [NO_SERVER]
