"""Run `ish --ask` end to end, with a fake embedder and a fake model."""

from pathlib import Path

import pytest

from ish import bootstrap
from ish.application.ports.language_model import (
    LanguageModelError,
    Passage,
    Reply,
)
from ish.interfaces.cli.main import main

FLAGS = ["--no-cache", "--no-git"]


class FakeEmbedder:
    model_name = "fake"

    def embed_documents(self, texts):
        return [[float("watchdog" in t), 1.0] for t in texts]

    def embed_query(self, text):
        return [1.0, 1.0]


class FakeModel:
    model_name = "fake-model"

    def __init__(self, covered: bool = True) -> None:
        self.covered = covered
        self.questions: list[str] = []

    def answer(self, question, sources):
        self.questions.append(question)
        watchdog = next(s.id for s in sources if "watchdog" in s.text)
        return Reply(
            passages=(
                Passage("The watchdog resets the board", (watchdog,)),
                Passage("."),
            ),
            covered=self.covered,
        )


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "boot.md").write_text("# Watchdog\n\nThe watchdog resets the board.\n")
    (docs / "other.md").write_text("# Colors\n\nThe sky is blue.\n")
    monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: FakeEmbedder())
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _model(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(bootstrap, "build_language_model", lambda settings: model)


class TestAsk:
    def test_the_answer_cites_its_source_by_number(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        model = FakeModel()
        _model(monkeypatch, model)
        code = main(["--ask", "what does the watchdog do", str(project), *FLAGS])
        out = capsys.readouterr().out
        assert code == 0
        assert model.questions == ["what does the watchdog do"]
        heading, answer, sources_heading, source = [
            block.strip() for block in out.split("\n\n")
        ]
        assert heading == "# what does the watchdog do"
        assert answer == "The watchdog resets the board[1]."
        assert sources_heading == "## Sources"
        link, heading = source.split("\n")
        assert link.startswith("1. [docs/boot.md:1-3](file://")
        assert link.endswith("/docs/boot.md#L1-3)")
        assert heading == "   Watchdog"

    def test_not_covered_lists_the_closest_sources_and_exits_2(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel(covered=False))
        code = main(["--ask", "what is the MTBF", str(project), *FLAGS])
        out = capsys.readouterr().out
        assert code == 2
        assert out.startswith("# what is the MTBF\n\n> The documents do not answer")
        assert "## Closest sources" in out
        assert "[docs/boot.md:1-3](file://" in out
        assert "[docs/other.md:1-3](file://" in out

    def test_a_question_is_required(self, project: Path, caplog) -> None:
        assert main(["--ask", str(project), *FLAGS]) == 1
        assert "--ask needs a question" in caplog.text

    def test_no_language_model_set_is_an_error(
        self, project: Path, capsys, caplog
    ) -> None:
        code = main(["--ask", "anything", str(project), *FLAGS])
        assert code == 1
        assert capsys.readouterr().out == ""
        assert "No language model is set" in caplog.text

    def test_a_model_error_is_reported(
        self, project: Path, monkeypatch, caplog
    ) -> None:
        def broken(settings):
            raise LanguageModelError("The Anthropic API did not answer within 5 s.")

        monkeypatch.setattr(bootstrap, "build_language_model", broken)
        assert main(["--ask", "anything", str(project), *FLAGS]) == 1
        assert "within 5 s" in caplog.text

    def test_a_plain_query_still_searches(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        """Without --ask the word "ask" is a query, as it always was."""

        def refuse(settings):
            raise AssertionError("a search built a language model")

        monkeypatch.setattr(bootstrap, "build_language_model", refuse)
        assert main(["ask", str(project), *FLAGS]) == 0
        assert "docs/" in capsys.readouterr().out
