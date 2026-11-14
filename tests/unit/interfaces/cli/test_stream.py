"""Test an answer shown on a terminal as the model writes it."""

from pathlib import Path

import pytest

from ish.application.answer import (
    Answer,
    AnswerBlock,
    AnswerPart,
    AnswerRow,
    AnswerTerm,
)
from ish.domain.chunk import Chunk
from ish.interfaces.cli.stream import CHANGED, StreamedAnswer
from ish.interfaces.format import answer_text

A = Chunk(Path("/d/spec/a.adoc"), "", "section", "asciidoc", "Overview > Reset", 1, 9)
B = Chunk(Path("/d/spec/b.adoc"), "", "section", "asciidoc", "Registers", 3, 4)
QUESTION = "how does it reset"


def link(chunk: Chunk) -> str:
    return f"file://{chunk.path}#L{chunk.start_line}"


def _answer(*blocks: AnswerBlock, covered: bool = True, searches=()) -> Answer:
    parts = tuple(p for b in blocks for p in b.parts)
    return Answer(
        parts=parts,
        covered=covered,
        sources=(A, B),
        blocks=blocks,
        terms=(AnswerTerm("CTRL", "Control register", (B,)),),
        searches=searches,
    )


SUMMARY = AnswerBlock("summary", parts=(AnswerPart("It resets at boot.", (A,)),))
STEPS = AnswerBlock("steps", heading="How", parts=(AnswerPart("Write CTRL.", (B, A)),))
TABLE = AnswerBlock(
    "table",
    heading="Values",
    columns=("Register", "Reset"),
    rows=(AnswerRow(("CTRL", "0x0"), (B,)),),
)
GAPS = AnswerBlock("gaps", parts=(AnswerPart("No source names the delay."),))


class Screen:
    def __init__(self) -> None:
        self.text = ""

    def __call__(self, text: str) -> None:
        self.text += text


@pytest.mark.parametrize("footnotes", [True, False])
@pytest.mark.parametrize("streamed", [0, 1, 2, 4])
def test_what_streams_and_the_rest_are_the_whole_answer(footnotes, streamed) -> None:
    """A terminal shows the text the whole answer is, however much streamed."""
    answer = _answer(SUMMARY, STEPS, TABLE, GAPS)
    screen = Screen()
    stream = StreamedAnswer(QUESTION, link, write=screen, footnotes=footnotes)
    for block in answer.blocks[:streamed]:
        stream.block(block)
    rest = stream.rest(answer, kind_of=lambda c: "doc")
    whole, _ = answer_text(
        answer, QUESTION, link, kind_of=lambda c: "doc", footnotes=footnotes
    )
    assert screen.text + rest == whole


def test_an_answer_that_does_not_start_with_what_streamed_comes_whole() -> None:
    screen = Screen()
    stream = StreamedAnswer(QUESTION, link, write=screen)
    stream.block(STEPS)
    answer = _answer(SUMMARY, STEPS, searches=("reset delay",))
    rest = stream.rest(answer)
    assert rest == f"{CHANGED}\n\n{answer_text(answer, QUESTION, link)[0]}"


def test_an_answer_not_covered_after_a_stream_comes_whole() -> None:
    screen = Screen()
    stream = StreamedAnswer(QUESTION, link, write=screen)
    stream.block(SUMMARY)
    answer = _answer(covered=False)
    assert stream.rest(answer).startswith(f"{CHANGED}\n\n# {QUESTION}")


def test_each_piece_is_drawn_when_a_renderer_is_given() -> None:
    screen = Screen()
    stream = StreamedAnswer(
        QUESTION, link, write=screen, render=lambda text: f"<{text.strip()}>"
    )
    stream.block(SUMMARY)
    assert (
        screen.text
        == "<# how does it reset>\n<**Short answer:** It resets at boot. [^1]>\n"
    )
