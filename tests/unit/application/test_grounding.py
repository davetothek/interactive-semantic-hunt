"""Test the check of the claims of an answer against the words of its sources."""

from collections.abc import Sequence

from ish.application.grounding import (
    CHALLENGE_DONE,
    NO_QUOTE,
    NOT_STATED,
    OTHER_SOURCE,
    QUOTE_NOT_FOUND,
    SHORT_QUOTE,
    Failure,
    challenge_note,
    quote_problem,
    repair_note,
    review,
    words,
)
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

TEXTS = {
    "1": (
        "The DMA block sets the transfer window.\n"
        "It writes the burst length of the queue."
    ),
    "2": "WDT_EN | u1.0 | Watchdog enable setting",
}

WINDOW = Quote("1", "The DMA block sets the transfer window.")
SHIFT = Quote("1", "It writes the burst length of the queue.")


class Judge:
    """Give the verdict it was told for each claim, and record each call."""

    model_name = "judge"

    def __init__(self, **verdicts: str) -> None:
        self.verdicts = verdicts
        self.calls: list[list[Claim]] = []

    def check(self, claims: Sequence[Claim]) -> Verdicts:
        self.calls.append(list(claims))
        found = tuple(self.verdicts.get(c.text, STATED) for c in claims)
        return Verdicts(found, input_tokens=30, output_tokens=4, cost_usd=0.01)


def laid_out(*blocks: Block) -> Reply:
    return Reply(
        passages=tuple(p for b in blocks for p in b.passages),
        covered=True,
        blocks=blocks,
    )


class TestWords:
    def test_only_the_words_and_their_order_count(self) -> None:
        assert words("The *DMA* block,\n  sets: the window.") == (
            "the dma block sets the window"
        )


class TestQuoteProblem:
    """A quote holds only when its words stand in the source it names."""

    def test_a_quote_the_source_writes_holds(self) -> None:
        assert quote_problem(WINDOW, TEXTS) == ""

    def test_markup_and_line_breaks_do_not_matter(self) -> None:
        quote = Quote("1", "the  DMA block sets the\ntransfer window")
        assert quote_problem(quote, TEXTS) == ""

    def test_a_short_quote_of_plain_words_proves_nothing(self) -> None:
        assert quote_problem(Quote("1", "the transfer window"), TEXTS) == SHORT_QUOTE

    def test_a_short_quote_with_a_name_or_a_value_holds(self) -> None:
        """A row states a value in a few words, such as "0 : DMA burst"."""
        assert quote_problem(Quote("2", "WDT_EN u1.0"), TEXTS) == ""

    def test_one_word_proves_nothing_even_a_name(self) -> None:
        assert quote_problem(Quote("2", "WDT_EN"), TEXTS) == SHORT_QUOTE

    def test_words_the_source_does_not_write_fail(self) -> None:
        quote = Quote("1", "The firmware reads the global queue.")
        assert quote_problem(quote, TEXTS) == QUOTE_NOT_FOUND

    def test_a_quote_of_an_unknown_source_fails(self) -> None:
        assert quote_problem(Quote("9", WINDOW.text), TEXTS) == QUOTE_NOT_FOUND

    def test_a_quote_must_match_whole_words(self) -> None:
        """ "sets the transfer win" is no quote of "sets the transfer window"."""
        quote = Quote("1", "block sets the transfer win")
        assert quote_problem(quote, TEXTS) == QUOTE_NOT_FOUND


class TestReview:
    """A claim stays only when a quote of a source states or implies it."""

    def test_a_stated_claim_stays_and_is_marked(self) -> None:
        reply = laid_out(
            Block(
                "summary",
                passages=(Passage("DMA sets the window.", ("1",), (WINDOW,)),),
            )
        )
        checked = review(reply, TEXTS, Judge())
        assert checked.reply.covered
        assert checked.reply.blocks[0].passages[0].basis == STATED
        assert checked.failures == ()
        assert (checked.claims, checked.input_tokens, checked.cost_usd) == (1, 30, 0.01)

    def test_an_implied_claim_stays_marked_as_an_inference(self) -> None:
        claim = "The firmware sets the queue up."
        reply = laid_out(Block("prose", passages=(Passage(claim, ("1",), (SHIFT,)),)))
        checked = review(reply, TEXTS, Judge(**{claim: IMPLIED}))
        assert checked.reply.passages[0].basis == IMPLIED

    def test_a_claim_the_quote_does_not_state_leaves_the_answer(self) -> None:
        """A field comment about a queue does not show that code reads one."""
        claim = "DMA reads a global queue."
        reply = laid_out(
            Block(
                "prose",
                passages=(
                    Passage("DMA sets the window.", ("1",), (WINDOW,)),
                    Passage(claim, ("1",), (SHIFT,)),
                ),
            )
        )
        checked = review(reply, TEXTS, Judge(**{claim: UNSUPPORTED}))
        assert [p.text for p in checked.reply.passages] == ["DMA sets the window."]
        assert checked.failures == (Failure(claim, ("1",), NOT_STATED),)

    def test_a_claim_with_no_quote_fails_before_the_judge_reads_it(self) -> None:
        judge = Judge()
        reply = laid_out(Block("prose", passages=(Passage("Yes.", ("1",)),)))
        checked = review(reply, TEXTS, judge)
        assert checked.failures == (Failure("Yes.", ("1",), NO_QUOTE),)
        assert judge.calls == []
        assert not checked.reply.covered
        assert checked.reply.passages == checked.reply.blocks == ()

    def test_one_bad_quote_fails_a_claim_beside_a_good_one(self) -> None:
        made_up = Quote("1", "The firmware reads the global queue.")
        reply = laid_out(
            Block(
                "prose", passages=(Passage("DMA sets it.", ("1",), (WINDOW, made_up)),)
            )
        )
        checked = review(reply, TEXTS, Judge())
        assert checked.failures[0].problem == QUOTE_NOT_FOUND

    def test_the_judge_reads_every_claim_in_one_call(self) -> None:
        judge = Judge()
        reply = laid_out(
            Block(
                "summary",
                passages=(Passage("DMA sets the window.", ("1",), (WINDOW,)),),
            ),
            Block("prose", passages=(Passage("It writes a shift.", ("1",), (SHIFT,)),)),
        )
        review(reply, TEXTS, judge)
        assert len(judge.calls) == 1
        assert [c.quotes for c in judge.calls[0]] == [(WINDOW.text,), (SHIFT.text,)]

    def test_the_gaps_are_no_claims(self) -> None:
        judge = Judge()
        gaps = Block("gaps", passages=(Passage("The timing is not stated."),))
        reply = laid_out(
            Block(
                "summary",
                passages=(Passage("DMA sets the window.", ("1",), (WINDOW,)),),
            ),
            gaps,
        )
        checked = review(reply, TEXTS, judge)
        assert checked.reply.blocks[-1] == gaps
        assert len(judge.calls[0]) == 1

    def test_a_passage_of_a_layout_is_a_claim_even_with_no_source(self) -> None:
        reply = laid_out(Block("steps", passages=(Passage("Set WDT_EN."),)))
        checked = review(reply, TEXTS, Judge())
        assert checked.failures == (Failure("Set WDT_EN.", (), NO_QUOTE),)

    def test_a_row_is_a_claim_named_by_its_columns(self) -> None:
        judge = Judge()
        row = Row(("WDT_EN", "u1.0"), ("2",), (Quote("2", TEXTS["2"]),))
        reply = laid_out(Block("table", columns=("Register", "Unit"), rows=(row,)))
        checked = review(reply, TEXTS, judge)
        assert judge.calls[0][0].text == "Register: WDT_EN; Unit: u1.0"
        assert checked.reply.blocks[0].rows[0].basis == STATED
        assert checked.reply.passages[0].text == "WDT_EN | u1.0"

    def test_a_cell_past_the_columns_is_named_by_itself(self) -> None:
        judge = Judge()
        row = Row(("WDT_EN", "u1.0"), ("2",), (Quote("2", TEXTS["2"]),))
        review(
            laid_out(Block("table", columns=("Register",), rows=(row,))), TEXTS, judge
        )
        assert judge.calls[0][0].text == "Register: WDT_EN; u1.0"

    def test_a_table_that_keeps_no_row_becomes_prose(self) -> None:
        row = Row(("WDT_EN", "0x2"), ("2",))
        intro = Passage(
            "The register turns the watchdog on.", ("2",), (Quote("2", TEXTS["2"]),)
        )
        reply = laid_out(
            Block("table", passages=(intro,), columns=("R", "V"), rows=(row,))
        )
        checked = review(reply, TEXTS, Judge())
        assert checked.reply.blocks[0].kind == "prose"
        assert checked.reply.blocks[0].rows == ()
        assert checked.failures[0].problem == NO_QUOTE

    def test_a_reply_that_is_not_covered_is_left_as_it_is(self) -> None:
        judge = Judge()
        reply = Reply(passages=(Passage("Maybe."),), covered=False)
        assert review(reply, TEXTS, judge).reply == reply
        assert judge.calls == []


class TestAQuoteOfAnotherSource:
    """A claim shows the sources it cites, so its quote must come from one."""

    def test_a_quote_of_a_source_the_claim_does_not_cite_fails(self) -> None:
        judge = Judge()
        quote = Quote("2", TEXTS["2"])
        reply = laid_out(
            Block("prose", passages=(Passage("It is on.", ("1",), (quote,)),))
        )
        checked = review(reply, TEXTS, judge)
        assert checked.failures == (Failure("It is on.", ("1",), OTHER_SOURCE),)
        assert judge.calls == []


class TestTheCheckReadsTheContext:
    """A quote alone loses what its sentence refers to.

    A check that read only the quotes refused 11 of 43 claims that their
    sources state, among them the two that answered the question.
    """

    def test_each_quote_goes_to_the_check_with_its_source_around_it(self) -> None:
        judge = Judge()
        reply = laid_out(
            Block("prose", passages=(Passage("It writes a burst.", ("1",), (SHIFT,)),))
        )
        review(reply, TEXTS, judge)
        (claim,) = judge.calls[0]
        assert claim.context == (TEXTS["1"],)

    def test_a_long_source_gives_a_window_around_the_quote(self) -> None:
        from ish.application.grounding import CONTEXT_CHARS, context_of

        text = "x" * 2000 + " The DMA block sets the transfer window. " + "y" * 2000
        context = context_of(WINDOW, {"1": text})
        assert "The DMA block sets the transfer window." in context
        assert len(context) <= 2 * CONTEXT_CHARS + len(WINDOW.text) + 2

    def test_the_window_is_as_wide_as_asked(self) -> None:
        from ish.application.grounding import context_of

        text = "x" * 2000 + " The DMA block sets the transfer window. " + "y" * 2000
        context = context_of(WINDOW, {"1": text}, around=10)
        # Ten characters on each side of the words of the quote.
        assert (
            context == "x" * 9 + " The DMA block sets the transfer window. " + "y" * 8
        )

    def test_a_quote_found_across_markup_still_gets_its_context(self) -> None:
        from ish.application.grounding import context_of

        quote = Quote("1", "the DMA block sets the transfer window")
        assert "It writes the burst" in context_of(quote, TEXTS)

    def test_a_quote_with_no_source_is_its_own_context(self) -> None:
        from ish.application.grounding import context_of

        assert context_of(Quote("9", "no such source"), TEXTS) == "no such source"
        assert context_of(Quote("1", "!!"), TEXTS) == "!!"
        assert context_of(Quote("1", "absent words here"), TEXTS) == "absent words here"


class TestNativeCitations:
    """An answer with native citations comes as pieces between citations."""

    def test_a_short_uncited_piece_joins_two_claims_and_stays(self) -> None:
        reply = Reply(
            passages=(
                Passage("DMA sets the window", ("1",), (WINDOW,)),
                Passage(" and "),
                Passage("writes a shift.", ("1",), (SHIFT,)),
            ),
            covered=True,
        )
        checked = review(reply, TEXTS, Judge())
        assert [p.text for p in checked.reply.passages] == [
            "DMA sets the window",
            " and ",
            "writes a shift.",
        ]
        assert checked.reply.blocks == ()

    def test_an_uncited_piece_with_a_fact_is_a_claim(self) -> None:
        """A short piece such as "Yes, 5 V." states a fact and is checked."""
        reply = Reply(
            passages=(
                Passage("DMA sets the window.", ("1",), (WINDOW,)),
                Passage(" Yes, 5 V."),
            ),
            covered=True,
        )
        checked = review(reply, TEXTS, Judge())
        assert [p.text for p in checked.reply.passages] == ["DMA sets the window."]
        assert checked.failures == (Failure(" Yes, 5 V.", (), NO_QUOTE),)

    def test_a_long_uncited_piece_is_a_claim(self) -> None:
        claim = "The firmware also reads the queue each frame."
        reply = Reply(
            passages=(
                Passage("DMA sets the window.", ("1",), (WINDOW,)),
                Passage(claim),
            ),
            covered=True,
        )
        checked = review(reply, TEXTS, Judge())
        assert [p.text for p in checked.reply.passages] == ["DMA sets the window."]
        assert checked.failures == (Failure(claim, (), NO_QUOTE),)

    def test_joining_pieces_alone_are_no_answer(self) -> None:
        reply = Reply(
            passages=(Passage("In short:"), Passage("It reads it.", ("1",))),
            covered=True,
        )
        checked = review(reply, TEXTS, Judge())
        assert not checked.reply.covered
        assert checked.reply.passages == ()


class TestNotes:
    def test_the_repair_note_names_each_claim_and_its_reason(self) -> None:
        note = repair_note([Failure("DMA reads a queue.", ("1",), NOT_STATED)])
        assert f'- "DMA reads a queue.": {NOT_STATED}.' in note
        assert "leave the claim out" in note

    def test_the_challenge_note_quotes_the_draft_and_asks_for_searches(self) -> None:
        draft = Reply(
            passages=tuple(Passage(f"Claim {n}.") for n in range(7)) + (Passage(" "),),
            covered=True,
        )
        note = challenge_note(draft, 3)
        assert "- Claim 0." in note and "- Claim 4." in note
        assert "Claim 5." not in note
        assert "up to 3 searches" in note
        assert "not about the draft" in note

    def test_the_last_note_asks_for_the_answer(self) -> None:
        assert "Answer from all the sources" in CHALLENGE_DONE
