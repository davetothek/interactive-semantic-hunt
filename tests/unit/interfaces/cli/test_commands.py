"""Test the commands of the search program: search, ask, and pick."""

import sys
from pathlib import Path

import pytest

from ish.interfaces.cli.args import ASK, COMMANDS, PICK, SEARCH, CliArgs, build_parser
from ish.settings import option_names


class TestSearch:
    def test_the_first_word_is_the_query(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        args = CliArgs.from_args(["search", "parse config", "."])
        assert args.command == SEARCH
        assert args.query == "parse config"
        assert args.path == tmp_path.resolve()
        assert not args.ask
        assert not args.interactive

    @pytest.mark.parametrize("word", [*COMMANDS, "mcp", "serve", "complete"])
    def test_a_query_can_be_the_name_of_a_command(self, word: str) -> None:
        assert CliArgs.from_args(["search", word]).query == word

    def test_a_query_written_as_a_path_stays_a_query(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "src").mkdir()
        args = CliArgs.from_args(["search", "src/"])
        assert args.query == "src/"
        assert args.path == tmp_path.resolve()

    def test_a_query_is_required(self, capsys) -> None:
        with pytest.raises(SystemExit) as stopped:
            CliArgs.from_args(["search"])
        assert stopped.value.code == 2
        assert "usage: ish search [options] query [path]" in capsys.readouterr().err

    @pytest.mark.parametrize("flag", ["--ask", "--brief", "-i"])
    def test_the_flags_of_other_commands_are_refused(self, flag: str, capsys) -> None:
        with pytest.raises(SystemExit) as stopped:
            CliArgs.from_args(["search", "query", flag])
        assert stopped.value.code == 2
        assert flag in capsys.readouterr().err


class TestAsk:
    def test_the_first_word_is_the_question(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "docs").mkdir()
        args = CliArgs.from_args(["ask", "what resets the board", "docs"])
        assert args.command == ASK
        assert args.ask
        assert not args.brief
        assert args.query == "what resets the board"
        assert args.path == (tmp_path / "docs").resolve()

    def test_brief_asks_for_a_short_answer(self) -> None:
        args = CliArgs.from_args(["ask", "what resets the board", "--brief"])
        assert args.ask
        assert args.brief

    def test_a_question_is_required(self, capsys) -> None:
        with pytest.raises(SystemExit) as stopped:
            CliArgs.from_args(["ask"])
        assert stopped.value.code == 2
        assert "usage: ish ask [options] question [path]" in capsys.readouterr().err


class TestPick:
    def test_the_one_positional_is_the_path(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "log").mkdir()
        args = CliArgs.from_args(["pick", "log"])
        assert args.command == PICK
        assert args.interactive
        assert args.query == ""
        assert args.path == (tmp_path / "log").resolve()

    def test_the_path_defaults_to_the_working_directory(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert CliArgs.from_args(["pick"]).path == tmp_path.resolve()

    def test_a_second_positional_is_refused(self, capsys) -> None:
        with pytest.raises(SystemExit) as stopped:
            CliArgs.from_args(["pick", "query", "src"])
        assert stopped.value.code == 2
        assert "usage: ish pick [options] [path]" in capsys.readouterr().err


class TestTheLineWithNoCommand:
    def test_it_names_no_command(self) -> None:
        args = CliArgs.from_args(["parse config"])
        assert args.command == ""
        assert args.query == "parse config"

    @pytest.mark.parametrize("word", COMMANDS)
    def test_two_dashes_make_a_command_word_a_query(self, word: str) -> None:
        args = CliArgs.from_args(["--", word])
        assert args.command == ""
        assert args.query == word

    def test_a_command_is_read_only_as_the_first_word(self) -> None:
        args = CliArgs.from_args(["-v", "pick"])
        assert args.command == ""
        assert args.query == "pick"
        assert not args.interactive

    def test_the_flags_still_ask(self) -> None:
        assert CliArgs.from_args(["--ask", "why"]).ask
        brief = CliArgs.from_args(["--brief", "why"])
        assert brief.ask
        assert brief.brief
        assert CliArgs.from_args(["-i"]).interactive

    def test_the_command_line_of_the_process_is_the_default(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["ish", "ask", "why"])
        args = CliArgs.from_args()
        assert args.command == ASK
        assert args.query == "why"


class TestEveryCommand:
    @pytest.mark.parametrize("command", COMMANDS)
    def test_it_takes_every_option(self, command: str) -> None:
        destinations = {a.dest for a in build_parser(command)._actions}
        assert set(option_names()) <= destinations

    @pytest.mark.parametrize("command", COMMANDS)
    def test_an_option_reaches_the_settings(self, command: str) -> None:
        words = [command] if command == PICK else [command, "query"]
        assert CliArgs.from_args([*words, "--limit", "17"]).settings.limit == 17

    @pytest.mark.parametrize("command", COMMANDS)
    def test_the_version_names_the_program_alone(self, command: str, capsys) -> None:
        with pytest.raises(SystemExit) as stopped:
            CliArgs.from_args([command, "--version"])
        assert stopped.value.code == 0
        assert capsys.readouterr().out.startswith("ish ")

    def test_the_help_lists_every_command(self) -> None:
        text = build_parser().format_help()
        for word in (*COMMANDS, "mcp", "serve", "complete"):
            assert f"\n  {word} " in text

    def test_a_tree_to_forget_is_the_path(self, tmp_path: Path) -> None:
        gone = tmp_path / "gone"
        args = CliArgs.from_args(["search", str(gone), "--forget"])
        assert args.path == gone
        assert args.query == ""
