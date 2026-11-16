"""Test how an answer that read the tree cites the lines it read."""

from pathlib import Path

import pytest

from ish.interfaces.python.api import MAX_CITED_LINES, Ish
from ish.settings import Settings


@pytest.fixture()
def ish(tmp_path: Path):
    tree = tmp_path / "tree"
    (tree / "docs").mkdir(parents=True)
    (tree / "docs" / "clock.adoc").write_text(
        "".join(f"line {n}\n" for n in range(1, 11))
    )
    (tree / "docs" / "bin.adoc").write_bytes(b"\xff\xfe\xfa")
    (tmp_path / "outside.adoc").write_text("secret\n")
    with Ish(tree, settings=Settings(no_cache=True, git=False)) as session:
        yield session


class TestCitedLines:
    def test_a_range_gives_those_lines_as_a_chunk(self, ish: Ish) -> None:
        found = ish.cited_lines("docs/clock.adoc:3-4")
        assert found is not None
        chunk, text = found
        assert text == "line 3\nline 4\n"
        assert (chunk.start_line, chunk.end_line, chunk.kind) == (3, 4, "lines")
        assert chunk.path == ish.path.resolve() / "docs" / "clock.adoc"
        assert chunk.language == "asciidoc"
        assert chunk.symbol == "docs/clock.adoc:3-4"

    def test_one_line_needs_no_range(self, ish: Ish) -> None:
        found = ish.cited_lines(" docs/clock.adoc:7 ")
        assert found is not None and found[1] == "line 7\n"

    def test_a_range_past_the_end_stops_at_the_end(self, ish: Ish) -> None:
        found = ish.cited_lines("docs/clock.adoc:9-30")
        assert found is not None and found[0].end_line == 10

    @pytest.mark.parametrize(
        "ref",
        [
            "1",
            "docs/clock.adoc",
            "docs/clock.adoc:0-2",
            "docs/clock.adoc:5-4",
            "docs/clock.adoc:11-12",
            f"docs/clock.adoc:1-{MAX_CITED_LINES + 1}",
            "docs/gone.adoc:1-2",
            "docs/bin.adoc:1-1",
            "../outside.adoc:1-1",
        ],
    )
    def test_a_citation_that_names_no_lines_of_the_tree_gives_none(
        self, ish: Ish, ref: str
    ) -> None:
        """A citation outside the tree, or of lines that are not there, is no source."""
        assert ish.cited_lines(ref) is None

    def test_a_suffix_no_parser_reads_is_plain_text(self, ish: Ish) -> None:
        (ish.path / "notes.txt").write_text("a note\n")
        found = ish.cited_lines("notes.txt:1")
        assert found is not None and found[0].language == "text"
