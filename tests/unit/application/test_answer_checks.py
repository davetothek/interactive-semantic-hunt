"""Test how an answer checks its claims, repairs them, and challenges its draft."""

from collections.abc import Sequence

from ish.application.answer import (
    CHECKING,
    UNCONFIRMED,
    AnswerQuestion,
    AnswerStep,
)
from ish.application.grounding import CHALLENGE_DONE, NO_QUOTE, NOT_STATED
from ish.application.ports.language_model import (
    IMPLIED,
    STATED,
    UNSUPPORTED,
    Block,
    Claim,
    Passage,
    Quote,
    Reply,
    Row,
    Verdicts,
)
from tests.unit.application.test_answer import make_chunk, routed, searching

BOOT = make_chunk("boot", text="The watchdog starts at boot and resets the board.")
RESET = make_chunk("reset", text="A reset clears every register of the block.")

STARTS = Passage(
    "The watchdog starts at boot.",
    ("1",),
    (Quote("1", "The watchdog starts at boot"),),
)
GUESS = Passage(
    "The watchdog reads a queue.",
    ("1",),
    (Quote("1", "The watchdog starts at boot"),),
)


class Judge:
    """Give the verdict it was told for a claim, and STATED for the rest."""

    model_name = "judge"

    def __init__(self, **verdicts: str) -> None:
        self.verdicts = verdicts
        self.calls: list[list[str]] = []

    def check(self, claims: Sequence[Claim]) -> Verdicts:
        self.calls.append([c.text for c in claims])
        found = tuple(self.verdicts.get(c.text, STATED) for c in claims)
        return Verdicts(found, input_tokens=40, output_tokens=5, cost_usd=0.002)


class Writer:
    """Answer with the replies it holds, one a call, and record each step."""

    model_name = "writer"

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.answered = 0
        self.steps: list[tuple[list[str], int, tuple[str, ...]]] = []
        self.streamed: list[bool] = []

    def answer(self, question, sources, on_block=None):
        self.answered += 1
        self.streamed.append(on_block is not None)
        return self.replies.pop(0)

    def step(self, question, sources, searched, turns_left, on_block=None, notes=()):
        self.steps.append((list(searched), turns_left, tuple(notes)))
        self.streamed.append(on_block is not None)
        return self.replies.pop(0)


def answer_of(*passages: Passage, tokens: int = 100) -> Reply:
    return Reply(
        passages=passages,
        covered=True,
        input_tokens=tokens,
        output_tokens=10,
        cost_usd=0.01,
    )


class TestTheCheck:
    def test_a_claim_that_fails_is_set_apart_with_its_reason(self) -> None:
        writer = Writer(answer_of(STARTS, GUESS))
        result = AnswerQuestion(
            search=searching(BOOT),
            model=writer,
            judge=Judge(**{GUESS.text: UNSUPPORTED}),
            repairs=0,
        ).answer("what does the watchdog do")
        assert [p.text for p in result.parts] == [STARTS.text]
        assert result.parts[0].basis == STATED
        assert result.parts[0].quotes == ((BOOT, "The watchdog starts at boot"),)
        (unconfirmed,) = result.unconfirmed
        assert (unconfirmed.text, unconfirmed.basis, unconfirmed.note) == (
            GUESS.text,
            UNCONFIRMED,
            NOT_STATED,
        )
        assert unconfirmed.cites == (BOOT,)

    def test_the_check_is_reported_as_a_stage_with_its_cost(self) -> None:
        steps: list[AnswerStep] = []
        result = AnswerQuestion(
            search=searching(BOOT),
            model=Writer(answer_of(STARTS)),
            judge=Judge(),
            on_step=steps.append,
        ).answer("q")
        assert str(steps[-1]) == "judge checking the claims against their sources"
        assert steps[-1].stage == CHECKING
        assert CHECKING in dict(result.timings)
        assert (result.check_model, result.claims_checked) == ("judge", 1)
        assert (result.check_input_tokens, result.check_output_tokens) == (40, 5)
        assert result.cost_usd == 0.012

    def test_an_implied_claim_is_kept_as_an_inference(self) -> None:
        result = AnswerQuestion(
            search=searching(BOOT),
            model=Writer(answer_of(STARTS)),
            judge=Judge(**{STARTS.text: IMPLIED}),
        ).answer("q")
        assert result.parts[0].basis == IMPLIED

    def test_a_row_carries_its_basis_and_quotes(self) -> None:
        row = Row(("WDT", "boot"), ("1",), (Quote("1", "The watchdog starts at boot"),))
        block = Block("table", columns=("Block", "Starts"), rows=(row,))
        reply = Reply(passages=(), covered=True, blocks=(block,))
        result = AnswerQuestion(
            search=searching(BOOT), model=Writer(reply), judge=Judge()
        ).answer("q")
        (kept,) = result.blocks[0].rows
        assert kept.basis == STATED
        assert kept.quotes == ((BOOT, "The watchdog starts at boot"),)

    def test_an_answer_with_no_claim_that_holds_is_not_covered(self) -> None:
        result = AnswerQuestion(
            search=searching(BOOT),
            model=Writer(answer_of(Passage("Yes.", ("1",)))),
            judge=Judge(),
            repairs=0,
        ).answer("q")
        assert not result.covered
        assert result.parts == ()
        assert [p.note for p in result.unconfirmed] == [NO_QUOTE]

    def test_an_answer_that_is_not_covered_asks_no_judge(self) -> None:
        judge = Judge()
        reply = Reply(passages=(), covered=False)
        result = AnswerQuestion(
            search=searching(BOOT), model=Writer(reply), judge=judge
        ).answer("q")
        assert judge.calls == []
        assert result.unconfirmed == ()

    def test_a_checked_answer_streams_no_block(self) -> None:
        """A block on the screen before the check could hold a claim that fails."""
        writer = Writer(answer_of(STARTS))
        AnswerQuestion(
            search=searching(BOOT),
            model=writer,
            judge=Judge(),
            on_block=lambda block: None,
        ).answer("q")
        assert writer.streamed == [False]


class TestTheRepair:
    """The model gets a turn to fix the claims that fail."""

    def test_the_model_is_told_which_claims_failed_and_why(self) -> None:
        writer = Writer(answer_of(STARTS, GUESS), answer_of(STARTS, tokens=150))
        judge = Judge(**{GUESS.text: UNSUPPORTED})
        result = AnswerQuestion(
            search=searching(BOOT), model=writer, judge=judge, repairs=1
        ).answer("q")
        (searched, turns_left, notes) = writer.steps[0]
        assert (searched, turns_left) == ([], 0)
        assert f'"{GUESS.text}": {NOT_STATED}.' in notes[0]
        assert [p.text for p in result.parts] == [STARTS.text]
        assert result.unconfirmed == ()
        assert result.turns == 2
        assert (result.input_tokens, result.output_tokens) == (250, 20)
        assert result.claims_checked == 3
        assert judge.calls == [[STARTS.text, GUESS.text], [STARTS.text]]

    def test_what_still_fails_after_the_repairs_is_set_apart(self) -> None:
        writer = Writer(answer_of(STARTS, GUESS), answer_of(STARTS, GUESS))
        result = AnswerQuestion(
            search=searching(BOOT),
            model=writer,
            judge=Judge(**{GUESS.text: UNSUPPORTED}),
            repairs=1,
        ).answer("q")
        assert [p.text for p in result.unconfirmed] == [GUESS.text]
        assert len(writer.steps) == 1

    def test_a_repair_that_answers_nothing_keeps_the_checked_answer(self) -> None:
        """A repair turn that gives up takes back no claim that held."""
        writer = Writer(
            answer_of(STARTS, GUESS), Reply(passages=(), covered=False, input_tokens=7)
        )
        result = AnswerQuestion(
            search=searching(BOOT),
            model=writer,
            judge=Judge(**{GUESS.text: UNSUPPORTED}),
            repairs=2,
        ).answer("q")
        assert [p.text for p in result.parts] == [STARTS.text]
        assert [p.text for p in result.unconfirmed] == [GUESS.text]
        assert len(writer.steps) == 1
        assert (result.turns, result.input_tokens) == (2, 107)

    def test_no_repair_runs_when_every_claim_holds(self) -> None:
        writer = Writer(answer_of(STARTS))
        AnswerQuestion(
            search=searching(BOOT), model=writer, judge=Judge(), repairs=2
        ).answer("q")
        assert writer.steps == []

    def test_a_repair_may_search_when_the_answer_may(self) -> None:
        searches = Reply(passages=(), covered=False, searches=("reset",))
        fixed = answer_of(
            Passage(
                "A reset clears the registers.",
                ("2",),
                (Quote("2", "A reset clears every register"),),
            )
        )
        writer = Writer(answer_of(GUESS), searches, fixed)
        result = AnswerQuestion(
            search=routed(BOOT, reset=[RESET]),
            model=writer,
            turns=1,
            judge=Judge(**{GUESS.text: UNSUPPORTED}),
            repairs=1,
        ).answer("q")
        assert [turns_left for _, turns_left, _ in writer.steps] == [1, 1, 0]
        assert result.searches == ("reset",)
        assert [p.text for p in result.parts] == ["A reset clears the registers."]
        assert result.parts[0].cites == (RESET,)


class TestTheChallenge:
    """With turns left, the model searches against its first draft."""

    def test_the_draft_is_challenged_then_answered_from_all_sources(self) -> None:
        searches = Reply(passages=(), covered=False, searches=("reset",))
        writer = Writer(answer_of(STARTS), searches, answer_of(STARTS))
        result = AnswerQuestion(
            search=routed(BOOT, reset=[RESET]),
            model=writer,
            turns=2,
            challenge=True,
        ).answer("q")
        notes = [n for _, _, n in writer.steps]
        assert notes[0] == ()
        assert "Do not answer yet" in notes[1][0]
        assert f"- {STARTS.text}" in notes[1][0]
        assert notes[2] == (CHALLENGE_DONE,)
        assert result.searches == ("reset",)
        assert result.sources == (BOOT, RESET)
        assert result.turns == 3

    def test_a_model_that_answers_the_challenge_at_once_is_taken_at_its_word(
        self,
    ) -> None:
        writer = Writer(answer_of(STARTS), answer_of(STARTS))
        result = AnswerQuestion(
            search=searching(BOOT), model=writer, turns=2, challenge=True
        ).answer("q")
        assert len(writer.steps) == 2
        assert result.turns == 2

    def test_no_challenge_with_one_turn_whose_searches_could_not_run(self) -> None:
        """A challenge needs a turn to ask for searches and one to answer."""
        writer = Writer(answer_of(STARTS))
        result = AnswerQuestion(
            search=searching(BOOT), model=writer, turns=1, challenge=True
        ).answer("q")
        assert [notes for _, _, notes in writer.steps] == [()]
        assert result.turns == 1

    def test_no_challenge_when_no_turn_is_left(self) -> None:
        writer = Writer(answer_of(STARTS))
        AnswerQuestion(
            search=searching(BOOT), model=writer, turns=0, challenge=True
        ).answer("q")
        assert writer.steps == [] and writer.answered == 1

    def test_a_challenged_answer_streams_no_block(self) -> None:
        writer = Writer(answer_of(STARTS), answer_of(STARTS))
        AnswerQuestion(
            search=searching(BOOT),
            model=writer,
            turns=2,
            challenge=True,
            on_block=lambda block: None,
        ).answer("q")
        assert writer.streamed == [False, False]
