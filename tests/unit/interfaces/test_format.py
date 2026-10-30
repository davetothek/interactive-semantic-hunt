"""Test the output shapes."""

from pathlib import Path

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
    """An answer prints as text with numbered citations, then the sources."""

    @staticmethod
    def _chunk(name: str, line: int = 1):
        from ish.domain.chunk import Chunk

        return Chunk(
            Path(f"/d/{name}.adoc"), "t", "section", "asciidoc", name, line, line + 4
        )

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
        assert format_answer(answer, Path("/d")) == (
            "B says so[1], and A agrees[2][1].\n"
            "\n"
            "[1] b.adoc:10-14  section  b\n"
            "[2] a.adoc:1-5  section  a"
        )

    def test_not_covered_lists_the_closest_sources(self) -> None:
        from ish.application.answer import Answer
        from ish.interfaces.format import NOT_COVERED_TEXT, format_answer

        a = self._chunk("a")
        answer = Answer(parts=(), covered=False, sources=(a,))
        assert format_answer(answer, Path("/d")) == (
            f"{NOT_COVERED_TEXT}\n\na.adoc:1-5  section  a"
        )
