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


class FakeReranker:
    """Choose the given ids, and record what it was shown."""

    model_name = "picker"

    def __init__(self, ids: tuple[str, ...]) -> None:
        self.ids = ids
        self.calls: list[tuple[str, list[Source], int]] = []

    def select(self, question, candidates, limit):
        from ish.application.ports.language_model import Selection

        self.calls.append((question, list(candidates), limit))
        return Selection(ids=self.ids, input_tokens=300, output_tokens=5)


class TestRerank:
    """A wide search ranks the right source too low for a narrow one.

    Over a private tree, the expected section ranked 15th to 32nd for five
    of 21 questions, behind test cases that share the wording. A model that
    picks ten of fifty from the start of each chunk found all 21.
    """

    def test_the_answer_reads_only_the_chosen_chunks_in_the_chosen_order(
        self,
    ) -> None:
        a, b, c = (
            make_chunk("a", "A text"),
            make_chunk("b", "B text"),
            make_chunk("c", "C text"),
        )
        model, picker = FakeModel(), FakeReranker(("3", "1", "9"))
        result = AnswerQuestion(
            search=searching(a, b, c), model=model, reranker=picker, pick=2
        ).answer("q")
        assert [s.text for s in model.calls[0][1]] == ["C text", "A text"]
        assert result.sources == (c, a)
        assert (result.rerank_input_tokens, result.rerank_output_tokens) == (300, 5)
        assert picker.calls[0][2] == 2

    def test_the_picker_sees_the_start_of_each_readable_chunk(
        self, tmp_path: Path
    ) -> None:
        long = make_chunk("long", "word " * 200)
        blank = make_chunk("blank", "   ")
        picker = FakeReranker(("1",))
        AnswerQuestion(
            search=searching(long, blank), model=FakeModel(), reranker=picker
        ).answer("q")
        (shown,) = picker.calls[0][1]
        assert shown.id == "1"
        assert len(shown.text) == 300
        assert shown.title.endswith("long")

    def test_nothing_readable_asks_no_picker_and_no_model(self) -> None:
        model, picker = FakeModel(), FakeReranker(("1",))
        result = AnswerQuestion(
            search=searching(make_chunk("blank", " ")), model=model, reranker=picker
        ).answer("q")
        assert (picker.calls, model.calls, result.covered) == ([], [], False)

    def test_a_picker_that_chooses_nothing_asks_no_model(self) -> None:
        model = FakeModel()
        result = AnswerQuestion(
            search=searching(make_chunk("a")),
            model=model,
            reranker=FakeReranker(()),
        ).answer("q")
        assert model.calls == []
        assert not result.covered
        assert result.rerank_input_tokens == 300


class FakeAgent:
    """Ask for the queued searches, one turn each, then answer."""

    model_name = "agent"

    def __init__(self, *turns: tuple[str, ...]) -> None:
        self.turns = list(turns)
        self.calls: list[tuple[list[Source], list[str], int]] = []

    def step(self, question, sources, searched, turns_left):
        self.calls.append((list(sources), list(searched), turns_left))
        if self.turns and turns_left > 0:
            return Reply(
                passages=(),
                covered=False,
                input_tokens=10,
                output_tokens=2,
                searches=self.turns.pop(0),
            )
        last = sources[-1].id
        return Reply(
            passages=(Passage("Found it.", (last,)),),
            covered=True,
            input_tokens=10,
            output_tokens=2,
        )


def routed(*first: Chunk, **by_query: list[Chunk]):
    def search(query: str) -> list[Match]:
        chunks = by_query.get(query.replace(" ", "_"), first)
        return [Match(c, 1.0) for c in chunks]

    return search


class TestFollowUpSearches:
    """The model may ask for searches before it answers, up to a limit."""

    def test_a_search_adds_new_chunks_and_the_answer_cites_them(self) -> None:
        start, found = make_chunk("start", "Spec."), make_chunk("found", "Handler.")
        agent = FakeAgent(("the handler",))
        result = AnswerQuestion(
            search=routed(start, the_handler=[start, found]), model=agent, turns=3
        ).answer("q")
        assert result.searches == ("the handler",)
        assert result.turns == 2
        assert result.sources == (start, found)
        assert result.parts[0].cites == (found,)
        assert [s.id for s in agent.calls[1][0]] == ["1", "2"]
        assert agent.calls[1][1] == ["the handler"]
        assert (result.input_tokens, result.output_tokens) == (20, 4)

    def test_the_last_turn_has_no_searches_left(self) -> None:
        agent = FakeAgent(("a",), ("b",), ("c",))
        result = AnswerQuestion(
            search=routed(make_chunk("x")), model=agent, turns=2
        ).answer("q")
        assert [turns_left for _, _, turns_left in agent.calls] == [2, 1, 0]
        assert result.searches == ("a", "b")
        assert result.covered

    def test_a_model_that_still_asks_on_the_last_turn_is_not_covered(self) -> None:
        class Asker:
            model_name = "asker"

            def step(self, question, sources, searched, turns_left):
                return Reply(passages=(), covered=True, searches=("more",))

        result = AnswerQuestion(
            search=routed(make_chunk("x")), model=Asker(), turns=1
        ).answer("q")
        assert not result.covered
        assert result.turns == 2

    def test_a_repeated_query_runs_once(self) -> None:
        runs: list[str] = []

        def search(query: str) -> list[Match]:
            runs.append(query)
            return [Match(make_chunk("x"), 1.0)]

        agent = FakeAgent(("same", "same", "other", "4th", "5th"), ("same",))
        result = AnswerQuestion(search=search, model=agent, turns=3).answer("q")
        assert runs == ["q", "same", "other", "4th"]
        assert result.searches == ("same", "other", "4th")

    def test_follow_up_chunks_keep_to_the_budget(self) -> None:
        start = make_chunk("start", "x" * 6)
        big, small = make_chunk("big", "y" * 9), make_chunk("small", "z" * 3)
        agent = FakeAgent(("more",))
        result = AnswerQuestion(
            search=routed(start, more=[big, small]),
            model=agent,
            turns=1,
            budget_chars=10,
        ).answer("q")
        assert result.sources == (start, small)

    def test_no_turns_answers_in_one_call(self) -> None:
        model = FakeModel()
        result = AnswerQuestion(search=searching(make_chunk("a")), model=model).answer(
            "q"
        )
        assert (result.turns, result.searches, len(model.calls)) == (1, (), 1)


class TestLayoutCarriesChunks:
    def test_blocks_and_rows_cite_chunks_and_drop_unknown_ids(self) -> None:
        from ish.application.ports.language_model import Block, Row

        a = make_chunk("a")
        reply = Reply(
            passages=(Passage("Short.", ("1",)),),
            covered=True,
            blocks=(
                Block("summary", passages=(Passage("Short.", ("1",)),)),
                Block(
                    "table",
                    heading="Regs",
                    columns=("N",),
                    rows=(Row(("x",), ("1", "9")),),
                ),
            ),
        )
        result = AnswerQuestion(search=searching(a), model=FakeModel(reply)).answer("q")
        summary, table = result.blocks
        assert summary.parts[0].cites == (a,)
        assert (table.heading, table.columns) == ("Regs", ("N",))
        assert table.rows[0].cells == ("x",)
        assert table.rows[0].cites == (a,)

    def test_a_reply_not_covered_has_no_layout(self) -> None:
        from ish.application.ports.language_model import Block

        reply = Reply(
            passages=(Passage("x"),),
            covered=False,
            blocks=(Block("summary", passages=(Passage("x"),)),),
        )
        result = AnswerQuestion(
            search=searching(make_chunk("a")), model=FakeModel(reply)
        ).answer("q")
        assert result.blocks == ()


class TestProgress:
    """A model call takes seconds, so the use case says what it is doing."""

    def test_each_step_renders_as_a_line(self) -> None:
        from ish.application.answer import (
            CHOOSING,
            FOLLOWING,
            READING,
            SEARCHING,
            WRITING,
            AnswerStep,
        )

        assert str(AnswerStep(SEARCHING)) == "Searching"
        assert str(AnswerStep(CHOOSING, count=50, model="haiku")) == (
            "haiku choosing what to read from 50 chunks"
        )
        assert str(AnswerStep(READING, count=10)) == "Reading 10 chunks"
        assert str(AnswerStep(FOLLOWING, query="the handler")) == (
            "Searching again: the handler"
        )
        assert str(AnswerStep(WRITING, model="haiku")) == "haiku writing the answer"
        assert str(AnswerStep(WRITING, model="haiku", turn=2, turns=4)) == (
            "haiku writing the answer, turn 2 of 4"
        )

    def test_a_plain_answer_searches_reads_and_writes(self) -> None:
        steps: list = []
        AnswerQuestion(
            search=searching(make_chunk("a")), model=FakeModel(), on_step=steps.append
        ).answer("q")
        assert [s.stage for s in steps] == ["searching", "reading", "writing"]
        assert steps[1].count == 1 and steps[2].model == "fake"

    def test_a_rerank_reports_what_it_chooses_from(self) -> None:
        steps: list = []
        AnswerQuestion(
            search=searching(make_chunk("a"), make_chunk("b")),
            model=FakeModel(),
            reranker=FakeReranker(("1",)),
            on_step=steps.append,
        ).answer("q")
        assert [s.stage for s in steps] == [
            "searching",
            "choosing",
            "reading",
            "writing",
        ]
        assert (steps[1].count, steps[1].model, steps[2].count) == (2, "picker", 1)

    def test_follow_up_turns_report_each_search_and_turn(self) -> None:
        steps: list = []
        agent = FakeAgent(("the handler",))
        AnswerQuestion(
            search=routed(make_chunk("x")), model=agent, turns=2, on_step=steps.append
        ).answer("q")
        assert [(s.stage, s.turn, s.query) for s in steps] == [
            ("searching", 0, ""),
            ("reading", 0, ""),
            ("writing", 1, ""),
            ("following", 0, "the handler"),
            ("writing", 2, ""),
        ]
        assert steps[2].turns == 3
