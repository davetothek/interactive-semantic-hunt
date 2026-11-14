"""Run `ish search`, `ish ask`, and `ish pick` end to end."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ish import bootstrap
from ish.domain.chunk import Chunk
from ish.interfaces.cli.main import main
from ish.settings import REMOTE_ENV_VAR
from tests.integration.cli.test_cli_ask import FLAGS, FakeEmbedder, FakeModel, _model

QUESTION = "what does the watchdog do"
ANSWER = "The watchdog resets the board [^1]."

# A server nothing listens on. A run that reaches for it fails.
NOWHERE = "http://127.0.0.1:9/mcp"


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "boot.md").write_text("# Watchdog\n\nThe watchdog resets the board.\n")
    (docs / "other.md").write_text("# Colors\n\nThe sky is blue.\n")
    monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: FakeEmbedder())
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


class TestSearch:
    def test_it_ranks_the_chunks_for_the_query(self, project: Path, capsys) -> None:
        assert main(["search", "watchdog", str(project), *FLAGS]) == 0
        first = capsys.readouterr().out.splitlines()[0]
        assert first == "[1.00] docs/boot.md:1-3  document  Watchdog"

    def test_an_empty_query_lists_every_chunk(self, project: Path, capsys) -> None:
        assert main(["search", "", str(project), *FLAGS]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines == [
            "docs/boot.md:1-3  document  Watchdog",
            "docs/other.md:1-3  document  Colors",
        ]


class TestAsk:
    def test_it_answers_with_the_source(
        self, project: Path, monkeypatch, capsys, caplog
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert main(["ask", QUESTION, str(project), *FLAGS]) == 0
        out = capsys.readouterr().out
        assert out.startswith(f"# {QUESTION}\n\n{ANSWER}\n\n## Sources")
        assert _warnings(caplog) == []

    def test_brief_prints_the_answer_alone(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert main(["ask", QUESTION, str(project), "--brief", *FLAGS]) == 0
        assert capsys.readouterr().out == "The watchdog resets the board.\n"

    def test_an_empty_question_names_the_command(self, project: Path, caplog) -> None:
        assert main(["ask", "", str(project), *FLAGS]) == 1
        assert "ish ask needs a question" in caplog.text

    def test_it_answers_on_this_machine_when_a_server_is_set(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        code = main(["ask", QUESTION, str(project), "--remote", NOWHERE, *FLAGS])
        assert code == 0
        assert ANSWER in capsys.readouterr().out


class TestTheReplacedFlags:
    def test_ask_warns_once_and_answers(
        self, project: Path, monkeypatch, capsys, caplog
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert main(["--ask", QUESTION, str(project), *FLAGS]) == 0
        assert ANSWER in capsys.readouterr().out
        assert _warnings(caplog) == [
            "--ask goes away in a later release. Use: ish ask QUESTION"
        ]

    def test_brief_names_the_brief_command(
        self, project: Path, monkeypatch, capsys, caplog
    ) -> None:
        _model(monkeypatch, FakeModel())
        assert main(["--ask", "--brief", QUESTION, str(project), *FLAGS]) == 0
        assert capsys.readouterr().out == "The watchdog resets the board.\n"
        assert _warnings(caplog) == [
            "--brief goes away in a later release. Use: ish ask QUESTION --brief"
        ]

    def test_a_search_gets_no_warning(self, project: Path, caplog) -> None:
        assert main(["watchdog", str(project), *FLAGS]) == 0
        assert _warnings(caplog) == []

    def test_remote_on_a_search_says_which_tree_it_read(
        self, project: Path, capsys, caplog
    ) -> None:
        # The flag never sent a search to a server. It still searches here.
        assert main(["watchdog", str(project), "--remote", NOWHERE, *FLAGS]) == 0
        first = capsys.readouterr().out.splitlines()[0]
        assert first == "[1.00] docs/boot.md:1-3  document  Watchdog"
        assert _warnings(caplog) == [
            "--remote goes away in a later release. "
            "This line reads the tree on this machine. "
            "To search the server, use: ish remote search QUERY --url URL"
        ]
        assert NOWHERE not in caplog.text


class TestAServerFromTheEnvironment:
    """`ISH_REMOTE` is for `ish remote`. A line without that word stays here."""

    def test_ask_answers_on_this_machine_and_says_so(
        self, project: Path, monkeypatch, capsys, caplog
    ) -> None:
        monkeypatch.setenv(REMOTE_ENV_VAR, NOWHERE)
        _model(monkeypatch, FakeModel())
        assert main(["--ask", QUESTION, str(project), *FLAGS]) == 0
        assert ANSWER in capsys.readouterr().out
        assert _warnings(caplog) == [
            "--ask goes away in a later release. Use: ish ask QUESTION. "
            "This answer comes from this machine. "
            "To ask the server, use: ish remote ask QUESTION"
        ]

    def test_brief_names_the_brief_command_of_the_server(
        self, project: Path, monkeypatch, capsys, caplog
    ) -> None:
        monkeypatch.setenv(REMOTE_ENV_VAR, NOWHERE)
        _model(monkeypatch, FakeModel())
        assert main(["--brief", QUESTION, str(project), *FLAGS]) == 0
        assert capsys.readouterr().out == "The watchdog resets the board.\n"
        assert _warnings(caplog) == [
            "--brief goes away in a later release. Use: ish ask QUESTION --brief. "
            "This answer comes from this machine. "
            "To ask the server, use: ish remote ask QUESTION --brief"
        ]

    @pytest.mark.parametrize("line", [["watchdog"], ["search", "watchdog"]])
    def test_a_search_reads_the_tree_of_this_machine(
        self, line: list[str], project: Path, monkeypatch, capsys, caplog
    ) -> None:
        monkeypatch.setenv(REMOTE_ENV_VAR, NOWHERE)
        assert main([*line, str(project), *FLAGS]) == 0
        first = capsys.readouterr().out.splitlines()[0]
        assert first == "[1.00] docs/boot.md:1-3  document  Watchdog"
        assert _warnings(caplog) == []

    def test_the_command_ask_answers_on_this_machine_with_no_warning(
        self, project: Path, monkeypatch, capsys, caplog
    ) -> None:
        monkeypatch.setenv(REMOTE_ENV_VAR, NOWHERE)
        _model(monkeypatch, FakeModel())
        assert main(["ask", QUESTION, str(project), *FLAGS]) == 0
        assert ANSWER in capsys.readouterr().out
        assert _warnings(caplog) == []


class TestPick:
    def test_it_opens_the_picker_and_prints_the_selection(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        chunk = Chunk(
            kind="function",
            language="python",
            symbol="dummy",
            path=Path("dummy.py"),
            start_line=10,
            end_line=15,
            text="pass",
        )
        app = MagicMock()
        app.return_value.run.return_value = (chunk, 0.99)
        monkeypatch.setattr("ish.interfaces.tui.app.IshApp", app)

        assert main(["pick", str(project), *FLAGS]) == 0
        assert "dummy.py:10" in capsys.readouterr().out
        assert app.call_args.args[1] == project.resolve()

    def test_a_path_that_does_not_exist_is_an_error(
        self, project: Path, caplog
    ) -> None:
        assert main(["pick", "watchdog", *FLAGS]) == 1
        assert "Path does not exist" in caplog.text


class TestIndex:
    """`ish index` builds or refreshes the index of a tree, and says how big."""

    def test_it_builds_the_index_and_names_its_size(
        self, project: Path, capsys
    ) -> None:
        cache = project / "idx"
        assert main(["index", str(project), "--cache-dir", str(cache)]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.splitlines()[-1] == (
            f"The index of {project.resolve()} holds 2 chunks."
        )
        assert list(cache.glob("*.db"))

    def test_a_second_run_finds_the_same_index(self, project: Path, capsys) -> None:
        cache = project / "idx"
        main(["index", "--cache-dir", str(cache)])
        capsys.readouterr()
        assert main(["index", "--cache-dir", str(cache)]) == 0
        assert capsys.readouterr().err.splitlines()[-1].endswith("holds 2 chunks.")

    def test_a_tree_ish_serve_keeps_is_named(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        from ish.interfaces import heartbeat
        from ish.interfaces.python.api import Ish

        kept = MagicMock(spec=heartbeat.Status)
        kept.pid, kept.tree, kept.refreshing = 4242, project, "Embedding 3 of 9"
        monkeypatch.setattr(Ish, "kept_by", lambda self: kept)
        assert main(["index", "--cache-dir", str(project / "idx")]) == 0
        assert capsys.readouterr().err.splitlines()[-1] == (
            # ish-serve does the work, so this run built nothing of its own.
            f"ish-serve 4242 keeps {project} current, Embedding 3 of 9. "
            "The index holds 0 chunks."
        )

    def test_a_tree_ish_serve_keeps_with_nothing_to_do(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        from ish.interfaces import heartbeat
        from ish.interfaces.python.api import Ish

        kept = MagicMock(spec=heartbeat.Status)
        kept.pid, kept.tree, kept.refreshing = 4242, project, ""
        monkeypatch.setattr(Ish, "kept_by", lambda self: kept)
        assert main(["index", "--cache-dir", str(project / "idx")]) == 0
        assert capsys.readouterr().err.splitlines()[-1] == (
            f"ish-serve 4242 keeps {project} current. The index holds 0 chunks."
        )

    def test_a_search_for_the_word_index_says_search(
        self, project: Path, capsys
    ) -> None:
        assert main(["search", "index", "--cache-dir", str(project / "idx")]) == 0
        assert "ish-serve" not in capsys.readouterr().err


class TestIndexOfOneChunk:
    def test_one_chunk_is_one_chunk(self, tmp_path: Path, monkeypatch, capsys) -> None:
        (tmp_path / "boot.md").write_text(
            "# Watchdog\n\nThe watchdog resets the board.\n"
        )
        monkeypatch.setattr(
            bootstrap, "build_embedder", lambda settings: FakeEmbedder()
        )
        monkeypatch.chdir(tmp_path)
        assert main(["index", "--cache-dir", str(tmp_path / "idx")]) == 0
        assert capsys.readouterr().err.splitlines()[-1].endswith("holds 1 chunk.")


class TestChat:
    """`ish chat` answers each line from the tree, until the input ends."""

    def _typed(self, monkeypatch, *lines: str) -> None:
        left = list(lines)

        def read(prompt: str) -> str:
            if not left:
                raise EOFError
            return left.pop(0)

        monkeypatch.setattr("builtins.input", read)

    def test_a_chat_answers_and_follows_up(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        model = FakeModel()
        _model(monkeypatch, model)
        self._typed(monkeypatch, QUESTION, "and how often?")
        assert main(["chat", "--cache-dir", str(project / "idx")]) == 0
        out, err = capsys.readouterr()
        assert out.count("The watchdog resets the board [1].") == 2
        assert "1. [docs/boot.md:1-3](file://" in out
        # The model wrote the search of the follow-up from the history,
        # and the chat says what it searched for.
        [(asked, history)] = model.queries
        assert asked == "and how often?" and history.startswith(f"Q: {QUESTION}")
        assert "Searched for: watchdog and how often?" in err
        first, second = model.questions
        assert first == QUESTION
        # The follow-up reads the history, under its heading.
        assert second.startswith("This is a chat.")
        assert second.endswith("Question: and how often?")

    def test_full_starts_with_full_answers(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        self._typed(monkeypatch, QUESTION)
        assert main(["chat", "--full", "--cache-dir", str(project / "idx")]) == 0
        assert capsys.readouterr().out.startswith(f"# {QUESTION}\n")

    def test_no_language_model_stops_the_chat(
        self, project: Path, monkeypatch, caplog
    ) -> None:
        self._typed(monkeypatch, QUESTION)
        assert main(["chat", "--cache-dir", str(project / "idx")]) == 1
        assert "Set language_model" in caplog.text
