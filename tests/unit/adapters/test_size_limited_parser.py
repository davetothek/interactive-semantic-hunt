"""Test the wrapper that keeps a chunk inside the embedding window."""

from pathlib import Path

import pytest

from ish.adapters.parser.limits import MAX_CHUNK_CHARS, SizeLimited
from ish.domain.chunk import Chunk


class Fake:
    """Return whatever chunks it was given."""

    language = "fake"
    suffixes = frozenset({".fake"})

    def __init__(self, chunks) -> None:
        self._chunks = chunks

    def parse(self, path, source):
        return self._chunks


def chunk(text: str, start: int = 1, symbol: str = "big") -> Chunk:
    return Chunk(
        path=Path("/p/a.fake"),
        text=text,
        kind="struct",
        language="fake",
        symbol=symbol,
        start_line=start,
        end_line=start + text.count("\n"),
    )


def parse(chunks, limit: int = 100):
    return SizeLimited(Fake(chunks), limit=limit).parse(Path("/p/a.fake"), "")


class TestPassesThrough:
    def test_a_small_chunk_is_untouched(self) -> None:
        one = chunk("line\n" * 3)
        assert parse([one]) == [one]

    def test_the_identity_of_the_parser_is_kept(self) -> None:
        wrapped = SizeLimited(Fake([]))
        assert wrapped.language == "fake"
        assert wrapped.suffixes == frozenset({".fake"})

    def test_nothing_in_nothing_out(self) -> None:
        assert parse([]) == []


class TestSplitting:
    def test_a_large_chunk_is_divided(self) -> None:
        pieces = parse([chunk("0123456789\n" * 30)], limit=100)
        assert len(pieces) > 1

    def test_every_piece_fits(self) -> None:
        pieces = parse([chunk("0123456789\n" * 100)], limit=100)
        assert pieces
        assert all(len(p.text) <= 100 for p in pieces)

    def test_no_text_is_lost(self) -> None:
        source = "".join(f"line {i}\n" for i in range(200))
        pieces = parse([chunk(source)], limit=100)
        assert "".join(p.text for p in pieces) == source

    def test_the_line_ranges_are_contiguous(self) -> None:
        source = "".join(f"line {i}\n" for i in range(200))
        pieces = parse([chunk(source, start=10)], limit=100)
        assert pieces[0].start_line == 10
        for before, after in zip(pieces, pieces[1:], strict=False):
            assert before.end_line + 1 == after.start_line

    def test_the_last_line_is_the_last_line(self) -> None:
        source = "".join(f"line {i}\n" for i in range(200))
        pieces = parse([chunk(source, start=1)], limit=100)
        assert pieces[-1].end_line == 200

    def test_the_symbol_travels_with_each_piece(self) -> None:
        """Each piece still belongs to the definition it came from."""
        pieces = parse([chunk("0123456789\n" * 50, symbol="tagS_BIG")], limit=100)
        assert {p.symbol for p in pieces} == {"tagS_BIG"}
        assert {p.kind for p in pieces} == {"struct"}

    def test_a_single_line_longer_than_the_window_stands_alone(self) -> None:
        """Breaking mid-token would garble it, so let it be."""
        pieces = parse([chunk("y" * 500 + "\n")], limit=100)
        assert len(pieces) == 1
        assert len(pieces[0].text) == 501

    def test_a_long_line_does_not_swallow_its_neighbours(self) -> None:
        source = "short\n" + "y" * 500 + "\n" + "short\n"
        pieces = parse([chunk(source)], limit=100)
        assert "".join(p.text for p in pieces) == source
        assert len(pieces) == 3


class TestTheWindow:
    def test_the_default_is_the_context_the_backend_serves(self) -> None:
        """2048 tokens is what Ollama gives an embedding model."""
        assert MAX_CHUNK_CHARS == 8_000

    @pytest.mark.parametrize("size", [8_001, 50_000, 3_000_000])
    def test_nothing_survives_the_wrapper_oversized(self, size: int) -> None:
        pieces = parse([chunk("ab\n" * (size // 3))], limit=MAX_CHUNK_CHARS)
        assert all(len(p.text) <= MAX_CHUNK_CHARS for p in pieces)
