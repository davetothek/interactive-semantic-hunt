"""Test the output shapes."""

from pathlib import Path

from ish.domain.chunk import Chunk
from ish.interfaces.format import (
    format_chunk_line,
    format_grep_line,
    format_result_line,
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
