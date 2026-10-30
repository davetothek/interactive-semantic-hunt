"""Test the answer use case against a fake search and a fake model."""

import logging
from collections.abc import Sequence
from pathlib import Path

import pytest

from ish.application.answer import AnswerQuestion
from ish.application.ports.language_model import Passage, Reply, Source
from ish.domain.chunk import Chunk
from ish.domain.match import Match


def make_chunk(name: str, text: str = "body", line: int = 1) -> Chunk:
    return Chunk(
        path=Path(f"/docs/{name}.adoc"),
        text=text,
        kind="section",
        language="asciidoc",
        symbol=name,
        start_line=line,
        end_line=line + 9,
    )


class FakeModel:
    """Record each call and answer with a fixed reply."""

    model_name = "fake"

    def __init__(self, reply: Reply | None = None) -> None:
        self.calls: list[tuple[str, list[Source]]] = []
        self.reply = reply or Reply(
            passages=(Passage("It starts at boot.", ("1",)),),
            covered=True,
            input_tokens=120,
            output_tokens=12,
        )

    def answer(self, question: str, sources: Sequence[Source]) -> Reply:
        self.calls.append((question, list(sources)))
        return self.reply


def searching(*chunks: Chunk):
    def search(question: str) -> list[Match]:
        return [Match(chunk, 1.0 - n / 10) for n, chunk in enumerate(chunks)]

    return search


class TestAnswer:
    def test_parts_cite_the_chunks_the_model_named(self) -> None:
        boot, other = make_chunk("boot"), make_chunk("other")
        answer = AnswerQuestion(search=searching(boot, other), model=FakeModel())
        result = answer.answer("how does it start")
        assert result.covered
        assert [p.text for p in result.parts] == ["It starts at boot."]
        assert result.parts[0].cites == (boot,)
        assert result.sources == (boot, other)

    def test_the_model_reads_only_the_search_results(self) -> None:
        model = FakeModel()
        boot = make_chunk("boot", text="The watchdog starts at boot.", line=12)
        AnswerQuestion(search=searching(boot), model=model).answer("  start?  ")
        question, sources = model.calls[0]
        assert question == "start?"
        assert sources == [
            Source(id="1", title="/docs/boot.adoc:12-21 boot", text=boot.text)
        ]

    def test_a_chunk_without_a_symbol_has_a_title_of_path_and_lines(self) -> None:
        model = FakeModel()
        plain = Chunk(Path("/docs/a.adoc"), "t", "section", "asciidoc", None, 3, 4)
        AnswerQuestion(search=searching(plain), model=model).answer("q")
        assert model.calls[0][1][0].title == "/docs/a.adoc:3-4"

    def test_the_cost_and_the_model_are_reported(self) -> None:
        result = AnswerQuestion(
            search=searching(make_chunk("a")), model=FakeModel()
        ).answer("q")
        assert (result.model, result.input_tokens, result.output_tokens) == (
            "fake",
            120,
            12,
        )

    def test_a_passage_may_cite_nothing(self) -> None:
        reply = Reply(passages=(Passage("In short:"),), covered=True)
        result = AnswerQuestion(
            search=searching(make_chunk("a")), model=FakeModel(reply)
        ).answer("q")
        assert result.parts[0].cites == ()


class TestNotCovered:
    """An answer the chunks do not support is not an answer."""

    def test_the_model_says_not_covered_and_the_closest_sources_remain(self) -> None:
        a, b = make_chunk("a"), make_chunk("b")
        reply = Reply(passages=(Passage("General knowledge.", ("1",)),), covered=False)
        result = AnswerQuestion(search=searching(a, b), model=FakeModel(reply)).answer(
            "q"
        )
        assert not result.covered
        assert result.parts == ()
        assert result.sources == (a, b)

    def test_no_search_results_asks_no_model(self) -> None:
        model = FakeModel()
        result = AnswerQuestion(search=searching(), model=model).answer("q")
        assert not result.covered
        assert result.sources == ()
        assert result.model == ""
        assert model.calls == []

    @pytest.mark.parametrize("question", ["", "   \n"])
    def test_an_empty_question_asks_nothing(self, question: str) -> None:
        model = FakeModel()
        searched: list[str] = []

        def search(q: str) -> list[Match]:
            searched.append(q)
            return []

        with pytest.raises(ValueError, match="empty"):
            AnswerQuestion(search=search, model=model).answer(question)
        assert searched == []
        assert model.calls == []


class TestBudget:
    def test_the_best_chunks_that_fit_are_kept_and_the_rest_counted(
        self, caplog
    ) -> None:
        model = FakeModel()
        first = make_chunk("first", text="x" * 6)
        long = make_chunk("long", text="x" * 10)
        short = make_chunk("short", text="x" * 4)
        caplog.set_level(logging.INFO, logger="ish.application.answer")
        result = AnswerQuestion(
            search=searching(first, long, short), model=model, budget_chars=10
        ).answer("q")
        assert result.sources == (first, short)
        assert result.dropped == 1
        assert [s.title.split()[-1] for s in model.calls[0][1]] == ["first", "short"]
        assert "1 chunks did not fit" in caplog.text

    def test_nothing_fits_asks_no_model(self) -> None:
        model = FakeModel()
        result = AnswerQuestion(
            search=searching(make_chunk("a", text="x" * 50)),
            model=model,
            budget_chars=10,
        ).answer("q")
        assert (result.covered, result.dropped, model.calls) == (False, 1, [])


class TestCitationsAreChecked:
    def test_a_citation_to_a_source_not_given_is_dropped_and_logged(
        self, caplog
    ) -> None:
        a = make_chunk("a")
        reply = Reply(passages=(Passage("Claim.", ("1", "7")),), covered=True)
        with caplog.at_level(logging.WARNING, logger="ish.application.answer"):
            result = AnswerQuestion(search=searching(a), model=FakeModel(reply)).answer(
                "q"
            )
        assert result.parts[0].cites == (a,)
        assert "cited source '7'" in caplog.text


class TestTextComesFromTheFile:
    """A stored index holds where a chunk is, not what it says.

    Every chunk a stored index returned had empty text, so the model read
    titles alone and answered nothing. The text is read from the file.
    """

    def _stored(self, path: Path, start: int, end: int) -> Chunk:
        return Chunk(path, "", "section", "asciidoc", "s", start, end)

    def test_the_model_reads_the_lines_from_the_file(self, tmp_path: Path) -> None:
        doc = tmp_path / "gate.adoc"
        doc.write_text("= Gate\n\nAdd the label.\nThen push.\n")
        model = FakeModel()
        AnswerQuestion(search=searching(self._stored(doc, 3, 4)), model=model).answer(
            "q"
        )
        assert model.calls[0][1][0].text == "Add the label.\nThen push.\n"

    def test_an_unreadable_chunk_is_left_out_and_counted(
        self, tmp_path: Path, caplog
    ) -> None:
        doc = tmp_path / "a.adoc"
        doc.write_text("= A\n\nText.\n")
        gone = self._stored(tmp_path / "gone.adoc", 1, 2)
        past_the_end = self._stored(doc, 50, 60)
        blank = self._stored(doc, 2, 2)
        kept = self._stored(doc, 3, 3)
        model = FakeModel()
        with caplog.at_level(logging.WARNING, logger="ish.application.answer"):
            result = AnswerQuestion(
                search=searching(gone, past_the_end, blank, kept), model=model
            ).answer("q")
        assert result.sources == (kept,)
        assert result.dropped == 3
        assert [s.text for s in model.calls[0][1]] == ["Text.\n"]
        assert "no readable text" in caplog.text

    def test_the_budget_counts_the_text_read(self, tmp_path: Path) -> None:
        doc = tmp_path / "a.adoc"
        doc.write_text("x" * 50 + "\n" + "y\n")
        long, short = self._stored(doc, 1, 1), self._stored(doc, 2, 2)
        result = AnswerQuestion(
            search=searching(long, short), model=FakeModel(), budget_chars=10
        ).answer("q")
        assert (result.sources, result.dropped) == ((short,), 1)
