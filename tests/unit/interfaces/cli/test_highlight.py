"""Test the terminal styles of an answer, which change no character of it."""

import pytest

from ish.application.answer import Answer, AnswerBlock, AnswerPart, AnswerRow
from ish.domain.chunk import Chunk
from ish.interfaces.cli.highlight import (
    BOLD,
    CYAN,
    GREY,
    UNDERLINE,
    YELLOW,
    grey,
    highlight,
    strip,
)
from ish.interfaces.format import format_answer

SOURCE = Chunk(
    path=__import__("pathlib").Path("/docs/spec/a.adoc"),
    text="x",
    kind="section",
    language="asciidoc",
    symbol="Overview > Exposure",
    start_line=12,
    end_line=27,
)


def _link(chunk: Chunk) -> str:
    return "https://docs.example.com/spec/a.adoc#L12-27"


def _answers() -> list[str]:
    part = AnswerPart("It uses `AEMODE` and **two** modes.", (SOURCE,))
    blocks = (
        AnswerBlock("summary", parts=(part,)),
        AnswerBlock(
            "table",
            heading="Registers",
            columns=("Register", "Reset"),
            rows=(AnswerRow(("AEMODE", "0x2"), (SOURCE,)),),
        ),
        AnswerBlock("gaps", parts=(AnswerPart("No source names the boundary."),)),
    )
    covered = Answer(parts=(part,), covered=True, sources=(SOURCE,), blocks=blocks)
    plain = Answer(parts=(part,), covered=True, sources=(SOURCE,))
    missing = Answer(parts=(), covered=False, sources=(SOURCE,))
    return [
        format_answer(a, "how is it split", _link) for a in (covered, plain, missing)
    ]


class TestNoCharacterChanges:
    """A copy from the terminal is the Markdown that was written."""

    @pytest.mark.parametrize("markdown", _answers())
    def test_an_answer_strips_back_to_itself(self, markdown: str) -> None:
        assert strip(highlight(markdown)) == markdown

    @pytest.mark.parametrize(
        "text",
        ["", "plain line", "a [^1] b [^22]", "| a | b |", "> quote", "    indented"],
    )
    def test_any_line_strips_back_to_itself(self, text: str) -> None:
        assert strip(highlight(text)) == text


class TestStyles:
    def test_a_heading_is_bold(self) -> None:
        assert f"{BOLD}{CYAN}Sources" in highlight("## Sources")

    def test_a_link_is_clickable_and_keeps_its_url_printed(self) -> None:
        out = highlight("[a.adoc:12-27](https://x.example/a.adoc#L12-27)")
        assert "\033]8;;https://x.example/a.adoc#L12-27\033\\" in out
        assert f"{UNDERLINE}a.adoc:12-27" in out
        assert "(https://x.example/a.adoc#L12-27)" in out

    def test_what_the_documents_leave_open_is_yellow(self) -> None:
        assert YELLOW in highlight("> - No source names it.")

    def test_a_citation_marker_is_cyan(self) -> None:
        assert f"{CYAN}[^3]" in highlight("It does. [^3]")

    def test_the_details_under_a_source_are_grey(self) -> None:
        assert highlight("    doc · Overview").startswith(f"    {GREY}doc")

    def test_grey(self) -> None:
        assert strip(grey("ish: 1.0 s")) == "ish: 1.0 s"
        assert grey("x").startswith(GREY)
