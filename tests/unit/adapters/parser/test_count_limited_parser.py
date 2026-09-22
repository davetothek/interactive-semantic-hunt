"""Test the wrapper that reads a file of thousands of chunks as one."""

import logging
from pathlib import Path

from ish.adapters.parser._limits import DEFAULT_MAX_CHUNKS, CountLimited
from ish.domain.chunk import Chunk


class Fake:
    """Yield one chunk per line, as a generated register map does."""

    language = "json"
    suffixes = frozenset({".json"})

    def parse(self, path, source):
        return [
            Chunk(
                path=path,
                text=line,
                kind="entry",
                language="json",
                symbol=f"reg{n}",
                start_line=n,
                end_line=n,
            )
            for n, line in enumerate(source.splitlines(), 1)
        ]


PATH = Path("/p/register_map.json")


def parse(lines: int, limit: int = 10, head: int = 100):
    source = "".join(f"line {n}\n" for n in range(1, lines + 1))
    return CountLimited(Fake(), limit=limit, head=head).parse(PATH, source), source


class TestPassesThrough:
    def test_a_file_within_the_limit_is_untouched(self) -> None:
        chunks, _source = parse(10)
        assert len(chunks) == 10

    def test_the_identity_of_the_parser_is_kept(self) -> None:
        wrapped = CountLimited(Fake())
        assert wrapped.language == "json"
        assert wrapped.suffixes == frozenset({".json"})

    def test_the_default_is_above_the_largest_hand_written_file(self) -> None:
        """A header split into 374 pieces. The register map yielded 32,768."""
        assert 374 < DEFAULT_MAX_CHUNKS < 32_768


class TestAGeneratedFileCostsOneChunk:
    """Verify a file of thousands of chunks is read as one.

    One 26.3 MB JSON register map produced 32,768 chunks in 76 s, hours
    of embedding for text nobody searches by meaning.
    """

    def test_one_chunk_stands_for_the_file(self) -> None:
        chunks, _source = parse(11)
        assert len(chunks) == 1
        assert chunks[0].kind == "file"
        assert chunks[0].symbol == "register_map.json"
        assert chunks[0].language == "json"

    def test_the_chunk_holds_the_head_of_the_file(self) -> None:
        chunks, source = parse(50, head=100)
        assert chunks[0].text == source[:100]
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == source[:100].count("\n") + 1

    def test_a_head_that_ends_on_a_line_counts_its_lines(self) -> None:
        chunks, _source = parse(50, head=len("line 1\nline 2\n"))
        assert chunks[0].end_line == 2

    def test_the_file_is_reported_once(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            parse(11)
        said = [r.message for r in caplog.records if "max_chunks" in r.message]
        assert len(said) == 1
        assert "register_map.json yields 11 chunks" in said[0]
        assert "Exclude it, or raise the limit" in said[0]

    def test_a_limit_below_one_still_keeps_one(self) -> None:
        chunks, _source = parse(3, limit=0)
        assert len(chunks) == 1
