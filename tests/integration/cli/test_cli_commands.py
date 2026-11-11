"""Run `ish search`, `ish ask`, and `ish pick` end to end."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ish import bootstrap
from ish.domain.chunk import Chunk
from ish.interfaces.cli.main import main
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
