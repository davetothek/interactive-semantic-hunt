"""Test the output shapes."""

from pathlib import Path

import pytest

from ish.domain.chunk import Chunk
from ish.interfaces.format import (
    display_path,
    format_chunk_line,
    format_grep_line,
    format_result_line,
    render,
    symbol_of,
)


def chunk(symbol: str | None = "run") -> Chunk:
    return Chunk(
        path=Path("/proj/src/a.py"),
        text="pass",
        kind="function",
        language="python",
        symbol=symbol,
        start_line=12,
        end_line=27,
    )


class TestGrepShape:
    """Verify the shape an editor picker parses.

    A picker splits on colons to find the file and the line it should
    open and preview, so those must come first and nothing before them
    may contain a colon.
    """

    def test_file_and_line_lead(self) -> None:
        line = format_grep_line(chunk())
        assert line.split(":")[1] == "12"

    def test_a_column_is_present(self) -> None:
        """An editor expects file:line:column even when column is one."""
        assert format_grep_line(chunk()).split(":")[2] == "1"

    def test_the_symbol_follows(self) -> None:
        assert format_grep_line(chunk()).endswith("function  run")

    def test_a_score_is_included_when_given(self) -> None:
        assert "[0.75]" in format_grep_line(chunk(), 0.75)

    def test_no_score_when_none(self) -> None:
        assert "[" not in format_grep_line(chunk())

    def test_an_unnamed_chunk_still_renders(self) -> None:
        assert "<anonymous>" in format_grep_line(chunk(None))


class TestPlainShape:
    """Verify the shape a person reads."""

    def test_line_range_is_shown(self) -> None:
        assert "12-27" in format_chunk_line(chunk())

    def test_a_result_leads_with_its_score(self) -> None:
        assert format_result_line(chunk(), 0.9).startswith("[0.90]")

    def test_symbol_of_names_the_unnamed(self) -> None:
        assert symbol_of(chunk(None)) == "<anonymous>"


class TestTheBase:
    """Verify a path is written from the directory a caller asked about.

    A server shared by several callers has a working directory that
    means nothing to them. An editor resolves a result against the
    directory it asked about, so the path is written from there.
    """

    def test_a_path_under_the_base_is_relative_to_it(self) -> None:
        assert display_path(Path("/proj/src/a.py"), Path("/proj")) == Path("src/a.py")

    def test_a_path_outside_the_base_stays_absolute(self) -> None:
        shown = display_path(Path("/proj/src/a.py"), Path("/other"))
        assert shown == Path("/proj/src/a.py")

    def test_without_a_base_the_working_directory_is_used(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert display_path(tmp_path / "src" / "a.py") == Path("src/a.py")
        assert display_path(Path("/proj/src/a.py")) == Path("/proj/src/a.py")

    def test_every_shape_takes_the_base(self) -> None:
        base = Path("/proj")
        assert render(chunk(), "plain", 0.5, base=base).startswith("[0.50] src/a.py:")
        assert render(chunk(), "plain", base=base).startswith("src/a.py:")
        assert render(chunk(), "grep", 0.5, base=base).startswith("src/a.py:12:1:")


class TestFormatAnswer:
    """An answer prints as Markdown, so it reads in a terminal and in an editor."""

    @staticmethod
    def _chunk(name: str, line: int = 1):
        from ish.domain.chunk import Chunk

        return Chunk(
            Path(f"/d/{name}.adoc"), "t", "section", "asciidoc", name, line, line + 4
        )

    @staticmethod
    def _link(chunk) -> str:
        return f"https://docs.example.com/{chunk.path.name}#{chunk.location.fragment()}"

    def test_each_source_is_numbered_once_in_order_of_first_citation(self) -> None:
        from ish.application.answer import Answer, AnswerPart
        from ish.interfaces.format import format_answer

        a, b = self._chunk("a"), self._chunk("b", 10)
        answer = Answer(
            parts=(
                AnswerPart("B says so", (b,)),
                AnswerPart(", and A agrees", (a, b)),
                AnswerPart("."),
            ),
            covered=True,
            sources=(a, b),
        )
        assert format_answer(answer, "Who  agrees?\n", self._link, Path("/d")) == (
            "# Who agrees?\n"
            "\n"
            "B says so[1], and A agrees[2][1].\n"
            "\n"
            "## Sources\n"
            "\n"
            "1. [b.adoc:10-14](https://docs.example.com/b.adoc#L10-14)\n"
            "   b\n"
            "2. [a.adoc:1-5](https://docs.example.com/a.adoc#L1-5)\n"
            "   a"
        )

    def test_not_covered_lists_the_closest_sources(self) -> None:
        from ish.application.answer import Answer
        from ish.interfaces.format import NOT_COVERED_TEXT, format_answer

        a = self._chunk("a")
        answer = Answer(parts=(), covered=False, sources=(a,))
        assert format_answer(answer, "MTBF?", self._link, Path("/d")) == (
            f"# MTBF?\n\n> {NOT_COVERED_TEXT}\n\n## Closest sources\n\n"
            "1. [a.adoc:1-5](https://docs.example.com/a.adoc#L1-5)\n"
            "   a"
        )

    def test_paragraphs_wrap_at_80_and_a_marker_stays_on_its_word(self) -> None:
        from ish.application.answer import Answer, AnswerPart
        from ish.interfaces.format import WIDTH, format_answer

        a = self._chunk("a")
        long = "word " * 40 + "end"
        answer = Answer(
            parts=(AnswerPart(long + "\n\n", (a,)), AnswerPart("Second.", (a,))),
            covered=True,
            sources=(a,),
        )
        out = format_answer(answer, "q", self._link, Path("/d"))
        body = out.split("## Sources")[0].strip().split("\n")[2:]
        assert all(len(line) <= WIDTH for line in body)
        assert body[-3].endswith("end[1]")
        assert body[-2:] == ["", "Second.[1]"]

    def test_a_long_heading_wraps_under_its_source(self) -> None:
        from ish.application.answer import Answer, AnswerPart
        from ish.domain.chunk import Chunk
        from ish.interfaces.format import WIDTH, format_answer

        heading = " > ".join(["Overview of the function"] * 6)
        chunk = Chunk(Path("/d/a.adoc"), "t", "section", "asciidoc", heading, 1, 2)
        answer = Answer(
            parts=(AnswerPart("x", (chunk,)),), covered=True, sources=(chunk,)
        )
        out = format_answer(answer, "q", self._link, Path("/d"))
        below = out.split("## Sources\n\n")[1].split("\n")[1:]
        assert len(below) > 1
        assert all(line.startswith("   ") and len(line) <= WIDTH for line in below)


class TestPartsAreSpaced:
    """Claude returned sentences as parts with no space between them."""

    @staticmethod
    def _answer(*texts: str):
        from ish.application.answer import Answer, AnswerPart

        return Answer(
            parts=tuple(AnswerPart(t) for t in texts), covered=True, sources=()
        )

    @pytest.mark.parametrize(
        ("texts", "joined"),
        [
            (("It stops.", "Then it resets."), "It stops. Then it resets."),
            (("It stops ", "and resets."), "It stops and resets."),
            (("It stops", ", then resets", "."), "It stops, then resets."),
            (("A", "", "B"), "A B"),
        ],
    )
    def test_parts_join_with_one_space(self, texts, joined) -> None:
        from ish.interfaces.format import number_citations

        assert number_citations(self._answer(*texts))[0] == joined


class TestParagraphs:
    """A part that ends a paragraph ends in newlines, after its markers."""

    def test_markers_stay_before_the_paragraph_break(self) -> None:
        from ish.application.answer import Answer, AnswerPart
        from ish.domain.chunk import Chunk
        from ish.interfaces.format import number_citations

        a = Chunk(Path("/d/a.adoc"), "t", "section", "asciidoc", "a", 1, 2)
        b = Chunk(Path("/d/b.adoc"), "t", "section", "asciidoc", "b", 3, 4)
        answer = Answer(
            parts=(
                AnswerPart("It resets.\n\n", (a,)),
                AnswerPart("It is refused in test mode.", (b,)),
            ),
            covered=True,
            sources=(a, b),
        )
        assert number_citations(answer)[0] == (
            "It resets.[1]\n\nIt is refused in test mode.[2]"
        )


class TestFollowUpSearchesAreShown:
    def test_the_searches_follow_the_sources(self) -> None:
        from ish.application.answer import Answer, AnswerPart
        from ish.domain.chunk import Chunk
        from ish.interfaces.format import format_answer

        a = Chunk(Path("/d/a.adoc"), "t", "section", "asciidoc", "a", 1, 2)
        answer = Answer(
            parts=(AnswerPart("x", (a,)),),
            covered=True,
            sources=(a,),
            searches=("the handler", "the register"),
        )
        out = format_answer(answer, "q", lambda c: "file:///d/a.adoc#L1-2", Path("/d"))
        assert out.endswith("## Follow-up searches\n\n- the handler\n- the register")
