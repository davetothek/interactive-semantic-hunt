"""Test the chat use case: the history, the two searches, and `/more`."""

from pathlib import Path

import pytest

from ish.application.answer import Answer, AnswerPart
from ish.application.chat import (
    CHARS_PER_TOKEN,
    HISTORY_HEADING,
    NOT_COVERED,
    Chat,
    Reading,
    Turn,
    cited,
    history_text,
    merged,
    plan,
    said,
)
from ish.domain.chunk import Chunk
from ish.domain.match import Match


def _chunk(name: str) -> Chunk:
    return Chunk(Path(name), "", "section", "markdown", name, 1, 3)


WATCHDOG = _chunk("watchdog.md")
RESET = _chunk("reset.md")
OTHER = _chunk("other.md")


def _answer(text: str, *cites: Chunk, covered: bool = True) -> Answer:
    parts = (AnswerPart(text, cites),) if covered else ()
    return Answer(parts=parts, covered=covered, sources=cites or (OTHER,))


class Asked:
    """An answer function that records each call and answers in turn."""

    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, Reading, bool]] = []

    def __call__(self, asked: str, reading: Reading, brief: bool) -> Answer:
        self.calls.append((asked, reading, brief))
        return self.answers.pop(0)


class TestATurn:
    def test_the_first_question_reads_no_history(self) -> None:
        asked = Asked(_answer("The watchdog resets the board.", WATCHDOG))
        turn = Chat(asked).ask("  what does the watchdog do  ")
        assert asked.calls == [
            ("what does the watchdog do", Reading(("what does the watchdog do",)), True)
        ]
        assert turn.question == "what does the watchdog do"
        assert turn.brief is True

    def test_a_follow_up_reads_the_history_and_searches_twice(self) -> None:
        asked = Asked(
            _answer("The watchdog resets the board.", WATCHDOG),
            _answer("Its reset value is 0x10.", RESET),
        )
        chat = Chat(asked)
        chat.ask("what does the watchdog do")
        chat.ask("and its reset value?")
        text, reading, _brief = asked.calls[1]
        assert reading == Reading(
            ("and its reset value?", "what does the watchdog do and its reset value?"),
            (WATCHDOG,),
        )
        assert text == (
            f"{HISTORY_HEADING}\n\n"
            "Q: what does the watchdog do\nA: The watchdog resets the board.\n\n"
            "Question: and its reset value?"
        )

    def test_full_mode_asks_for_the_full_answer(self) -> None:
        asked = Asked(_answer("Long.", WATCHDOG))
        Chat(asked, brief=False).ask("why")
        assert asked.calls[0][2] is False

    def test_an_empty_question_is_refused(self) -> None:
        asked = Asked()
        with pytest.raises(ValueError, match="The question is empty."):
            Chat(asked).ask("   ")
        assert asked.calls == []


class TestMore:
    def test_more_reads_what_the_last_answer_read_in_full(self) -> None:
        brief = Answer(
            parts=(AnswerPart("Short.", (WATCHDOG,)),),
            covered=True,
            sources=(WATCHDOG, RESET),
        )
        asked = Asked(brief, _answer("The long answer.", WATCHDOG))
        chat = Chat(asked)
        first = chat.ask("why")
        more = chat.more()
        assert asked.calls[1] == (first.asked, Reading(only=(WATCHDOG, RESET)), False)
        assert (more.question, more.brief) == ("why", False)
        # The full answer stands for the turn in the history.
        assert chat.turns == (more,)

    def test_more_before_an_answer_says_so(self) -> None:
        with pytest.raises(LookupError, match="There is no answer yet"):
            Chat(Asked()).more()


class TestNew:
    def test_new_forgets_the_history(self) -> None:
        asked = Asked(_answer("A.", WATCHDOG), _answer("B.", RESET))
        chat = Chat(asked)
        chat.ask("first")
        chat.new()
        assert chat.last is None
        chat.ask("second")
        assert asked.calls[1][0] == "second"
        assert asked.calls[1][1] == Reading(("second",))


def _turn(question: str, text: str, covered: bool = True) -> Turn:
    return Turn(question, _answer(text, WATCHDOG, covered=covered), question, True)


class TestHistory:
    def test_an_answer_the_sources_did_not_cover_says_so(self) -> None:
        assert said(_answer("", covered=False)) == NOT_COVERED
        assert history_text([_turn("why", "", covered=False)], 1000) == (
            f"Q: why\nA: {NOT_COVERED}"
        )

    def test_said_keeps_the_cited_parts_with_no_space_before_a_stop(self) -> None:
        answer = Answer(
            parts=(
                AnswerPart(" One ", (WATCHDOG,)),
                AnswerPart(". ", (WATCHDOG,)),
                AnswerPart("  ", (RESET,)),
                AnswerPart("Two", (RESET,)),
            ),
            covered=True,
            sources=(),
        )
        assert said(answer) == "One. Two"

    def test_the_recent_turns_stay_whole_and_older_keep_their_questions(self) -> None:
        turns = [_turn(f"q{n}", "x" * 40) for n in range(1, 6)]
        # Each whole turn is about 50 characters. Two fit, then questions.
        text = history_text(turns, 120)
        assert text == (
            "Earlier questions: q1 | q2 | q3\n\n"
            f"Q: q4\nA: {'x' * 40}\n\n"
            f"Q: q5\nA: {'x' * 40}"
        )

    def test_the_newest_turn_stays_whole_beyond_the_budget(self) -> None:
        text = history_text([_turn("q1", "a"), _turn("q2", "y" * 500)], 100)
        assert text == f"Q: q2\nA: {'y' * 500}"

    def test_the_oldest_questions_go_first(self) -> None:
        turns = [_turn("an old question", "x"), _turn("q2", "x" * 40), _turn("q3", "a")]
        assert history_text(turns, 60).startswith("Q: q2")

    def test_no_turn_is_no_history(self) -> None:
        assert history_text([], 100) == ""

    def test_the_chat_counts_its_budget_in_tokens(self) -> None:
        # A whole turn is about 110 characters, and 248 fit: two turns
        # whole, and the question of the oldest.
        asked = Asked(*[_answer("x" * 100, WATCHDOG) for _ in range(4)])
        chat = Chat(asked, budget_tokens=250 // CHARS_PER_TOKEN)
        for n in range(4):
            chat.ask(f"q{n}")
        assert "Earlier questions: q0" in asked.calls[3][0]


class TestSearch:
    def test_cited_keeps_each_chunk_once_in_order(self) -> None:
        answer = Answer(
            parts=(AnswerPart("a", (RESET, WATCHDOG)), AnswerPart("b", (RESET,))),
            covered=True,
            sources=(),
        )
        assert cited(answer) == (RESET, WATCHDOG)

    def test_merged_keeps_the_best_score_and_adds_the_carry(self) -> None:
        found = merged(
            [[Match(WATCHDOG, 0.5), Match(RESET, 0.3)], [Match(RESET, 0.9)]],
            [WATCHDOG, OTHER],
            limit=5,
        )
        assert found == [Match(RESET, 0.9), Match(WATCHDOG, 0.5), Match(OTHER, 0.0)]

    def test_merged_keeps_the_best_within_the_limit(self) -> None:
        found = merged([[Match(WATCHDOG, 0.5), Match(RESET, 0.3)]], [], limit=1)
        assert found == [Match(WATCHDOG, 0.5)]


class TestPlan:
    def _search(self, ran: list[str]):
        def search(text: str) -> list[Match]:
            ran.append(text)
            return [Match(WATCHDOG, 0.5)] if text == "a" else [Match(RESET, 0.7)]

        return search

    def test_the_first_search_runs_the_queries_and_later_ones_as_asked(self) -> None:
        ran: list[str] = []
        reranker = object()
        found, kept, turns = plan(
            Reading(("a", "b"), (OTHER,)), self._search(ran), 10, reranker, 2
        )
        assert (kept, turns) == (reranker, 2)
        assert found("the question with the history") == [
            Match(RESET, 0.7),
            Match(WATCHDOG, 0.5),
            Match(OTHER, 0.0),
        ]
        assert found("a follow-up") == [Match(RESET, 0.7)]
        assert ran == ["a", "b", "a follow-up"]

    def test_only_reads_those_chunks_with_no_rerank_and_no_follow_up(self) -> None:
        ran: list[str] = []
        found, kept, turns = plan(
            Reading(only=(WATCHDOG, RESET)), self._search(ran), 10, object(), 3
        )
        assert (kept, turns) == (None, 0)
        assert found("anything") == [Match(WATCHDOG, 1.0), Match(RESET, 1.0)]
        assert ran == []


class TestAClaimWithNoSourceStaysOutOfTheHistory:
    """The model reads the history again, so only a cited part may enter it.

    A part with no citation printed in a reply would otherwise come back
    to the model in the next turn as something the chat had said.
    """

    def test_an_uncited_part_is_left_out(self) -> None:
        answer = Answer(
            parts=(
                AnswerPart("The board resets after 5 seconds"),
                AnswerPart("The watchdog resets the board.", (WATCHDOG,)),
            ),
            covered=True,
            sources=(WATCHDOG,),
        )
        assert said(answer) == "The watchdog resets the board."

    def test_an_answer_that_cites_nothing_says_so(self) -> None:
        from ish.application.chat import NO_CLAIM

        answer = Answer(parts=(AnswerPart("Made up."),), covered=True, sources=())
        assert said(answer) == NO_CLAIM

    def test_the_next_turn_reads_no_uncited_claim(self) -> None:
        first = Answer(
            parts=(AnswerPart("Five seconds."), AnswerPart("It resets.", (WATCHDOG,))),
            covered=True,
            sources=(WATCHDOG,),
        )
        asked = Asked(first, _answer("Yes.", RESET))
        chat = Chat(asked)
        chat.ask("what does it do")
        chat.ask("how long")
        assert "Five seconds." not in asked.calls[1][0]
        assert "It resets." in asked.calls[1][0]
