"""Test what a terminal shows of an answer: rendered on request, and the grey."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from ish.application.answer import Answer, AnswerBlock, AnswerPart, AnswerRow
from ish.domain.chunk import Chunk
from ish.interfaces.cli.terminal import GREY, grey, rendered
from ish.interfaces.format import format_answer

SOURCE = Chunk(
    path=Path("/docs/spec/a.adoc"),
    text="x",
    kind="section",
    language="asciidoc",
    symbol="Overview > Exposure",
    start_line=12,
    end_line=27,
)
URL = "https://docs.example.com/spec/a.adoc#L12-27"
ESCAPE = "\033"


def _markdown(answer: Answer) -> str:
    return format_answer(answer, "how is it split", lambda chunk: URL)


def _covered() -> str:
    part = AnswerPart("It uses `AEMODE` and **two** modes.", (SOURCE,))
    blocks = (
        AnswerBlock("summary", parts=(part,)),
        AnswerBlock(
            "table",
            heading="Registers",
            columns=("Register", "Reset"),
            rows=(AnswerRow(("AEMODE", "0x2"), (SOURCE,)),),
        ),
        AnswerBlock("steps", parts=(AnswerPart("Write the mode.", (SOURCE,)),)),
        AnswerBlock("gaps", parts=(AnswerPart("No source names the boundary."),)),
    )
    return _markdown(
        Answer(parts=(part,), covered=True, sources=(SOURCE,), blocks=blocks)
    )


def _not_covered() -> str:
    return _markdown(Answer(parts=(), covered=False, sources=(SOURCE,)))


@pytest.fixture(autouse=True)
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name a terminal that takes color, whatever runs the suite."""
    monkeypatch.setenv("TERM", "xterm-256color")


class TestRenderedWithoutColor:
    """The layout alone, with no escape code."""

    def test_it_holds_no_escape_code(self) -> None:
        assert ESCAPE not in rendered(_covered(), width=80, color=False)

    def test_the_markup_is_gone(self) -> None:
        out = rendered(_covered(), width=80, color=False)
        for mark in ("# ", "**", "`", "|", "[^", "> "):
            assert mark not in out
        assert "Short answer: It uses AEMODE and two modes. [1]" in out

    def test_a_table_row_stays_on_one_line(self) -> None:
        out = rendered(_covered(), width=80, color=False)
        assert any(line.split() == ["AEMODE", "0x2", "[1]"] for line in out.split("\n"))

    def test_a_source_is_a_numbered_item_with_a_line_for_each_detail(self) -> None:
        lines = rendered(_covered(), width=80, color=False).split("\n")
        at = lines.index(f" 1 a.adoc:12-27 ({URL})")
        assert lines[at + 1 : at + 3] == ["   Overview › Exposure", "   /docs/spec/"]

    def test_a_closest_source_keeps_its_details_apart(self) -> None:
        lines = rendered(_not_covered(), width=80, color=False).split("\n")
        at = lines.index(f" 1 a.adoc:12-27 ({URL})")
        assert lines[at + 1 : at + 3] == ["   Overview › Exposure", "   /docs/spec/"]

    def test_no_line_passes_the_width_or_ends_in_a_space(self) -> None:
        text = "A sentence of prose that runs on. " * 8
        lines = rendered(text, width=40, color=False).split("\n")
        assert len(lines) > 1
        assert all(len(line) <= 40 and line == line.rstrip() for line in lines)

    def test_a_link_to_a_file_prints_its_url(self) -> None:
        out = rendered("[a.md:1-2](file:///x/a.md#L1-2)", width=80, color=False)
        assert out == "a.md:1-2 (file:///x/a.md#L1-2)"

    def test_a_code_span_that_reads_as_a_marker_stays(self) -> None:
        assert "[^1]" in rendered("Match `[^1]` here.", width=80, color=False)


class TestRenderedWithColor:
    def test_it_styles_the_text(self) -> None:
        assert f"{ESCAPE}[" in rendered(_covered(), width=80, color=True)

    def test_a_source_opens_with_a_click_and_hides_its_url(self) -> None:
        out = rendered(_covered(), width=80, color=True)
        assert f";{URL}{ESCAPE}\\" in out
        assert f"({URL})" not in out

    def test_a_marker_opens_its_source(self) -> None:
        out = rendered(
            f"It does. [^1]\n\n[^1]: [a.adoc:1-2]({URL})", width=80, color=True
        )
        marker = out.split("\n")[0]
        assert f";{URL}{ESCAPE}\\" in marker and "[1]" in marker

    def test_a_link_to_a_file_opens_the_file(self) -> None:
        out = rendered("[a.md:1-2](file:///x/a.md#L1-2)", width=80, color=True)
        assert f";file:///x/a.md#L1-2{ESCAPE}\\" in out and "ish-file" not in out

    def test_a_marker_with_no_source_is_a_plain_number(self) -> None:
        out = rendered("It does. [^7]", width=80, color=True)
        assert "[7]" in out and f"{ESCAPE}]8" not in out


class TestPlainMarkers:
    """A chat marks a citation as ``[n]``, and lists its sources by number."""

    REPLY = f"It does. [1] [2]\n\n1. [a.adoc:1-2]({URL})\n2. [b.adoc:3-4]({URL}b)"

    def test_each_marker_opens_its_source(self) -> None:
        marker = rendered(self.REPLY, width=80, color=True).split("\n")[0]
        assert f";{URL}{ESCAPE}\\" in marker and f";{URL}b{ESCAPE}\\" in marker

    def test_brackets_on_a_word_or_before_a_link_are_no_marker(self) -> None:
        text = f"Read reg[1] or [1](https://e.invalid/x).\n\n1. [a.adoc:1-2]({URL})"
        first = rendered(text, width=80, color=True).split("\n")[0]
        assert URL not in first


class TestGreySources:
    """A chat shows its sources in grey, so the answer stands out."""

    def _lines(self, color: bool = True) -> list[str]:
        out = rendered(_covered(), width=80, color=color, grey_sources=True)
        return out.split("\n")

    def test_only_the_sources_are_grey(self) -> None:
        lines = self._lines()
        first = next(n for n, line in enumerate(lines) if "a.adoc:12-27" in line)
        assert all(GREY in line for line in lines[first : first + 3])
        assert not any(GREY in line for line in lines[:first])

    def test_a_line_ends_with_no_padding(self) -> None:
        assert not any(
            line.endswith(f" {ESCAPE}[0m") or line.endswith(" ")
            for line in self._lines()
        )

    def test_the_layout_is_that_of_one_rendering(self) -> None:
        def shown(text: str) -> str:
            return re.sub(r"\033\[[0-9;]*m|\033\]8;[^\033]*\033\\", "", text)

        whole = rendered(_covered(), width=80, color=True)
        assert shown("\n".join(self._lines())) == shown(whole)

    def test_with_no_color_it_is_the_plain_rendering(self) -> None:
        plain = rendered(_covered(), width=80, color=False)
        assert "\n".join(self._lines(color=False)) == plain


class TestGrey:
    def test_grey_wraps_a_line(self) -> None:
        assert grey("x").startswith(GREY) and grey("x").endswith("\033[0m")


class TestRichLoadsOnlyToRender:
    """A search and a plain answer never pay for the import of Rich."""

    def test_the_cli_imports_no_rich(self) -> None:
        code = (
            "import sys; "
            "import ish.interfaces.cli.main, ish.interfaces.cli.terminal; "
            "bad = [m for m in sys.modules if m.split('.')[0] == 'rich']; "
            "assert not bad, bad"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
