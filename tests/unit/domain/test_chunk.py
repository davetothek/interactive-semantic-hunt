"""Test the Chunk domain model."""

from pathlib import Path

import pytest

from ish.domain.chunk import Chunk


@pytest.fixture()
def sample_chunk() -> Chunk:
    """Build a representative Chunk for reuse across tests."""
    return Chunk(
        path=Path("src/example.py"),
        text="def greet():\n    return 'hello'\n",
        kind="function",
        language="python",
        symbol="greet",
        start_line=1,
        end_line=2,
    )


class TestChunkCreation:
    """Verify that Chunk stores all fields correctly."""

    def test_path(self, sample_chunk: Chunk) -> None:
        assert sample_chunk.path == Path("src/example.py")

    def test_text(self, sample_chunk: Chunk) -> None:
        assert sample_chunk.text == "def greet():\n    return 'hello'\n"

    def test_kind(self, sample_chunk: Chunk) -> None:
        assert sample_chunk.kind == "function"

    def test_symbol(self, sample_chunk: Chunk) -> None:
        assert sample_chunk.symbol == "greet"

    def test_start_line(self, sample_chunk: Chunk) -> None:
        assert sample_chunk.start_line == 1

    def test_end_line(self, sample_chunk: Chunk) -> None:
        assert sample_chunk.end_line == 2


class TestChunkNullableSymbol:
    """Verify that symbol accepts None for anonymous chunks."""

    def test_symbol_none(self) -> None:
        chunk = Chunk(
            path=Path("mod.py"),
            text="x = 1",
            kind="module",
            language="python",
            symbol=None,
            start_line=1,
            end_line=1,
        )
        assert chunk.symbol is None


class TestChunkFrozen:
    """Verify immutability — domain objects must not change after creation."""

    def test_cannot_set_path(self, sample_chunk: Chunk) -> None:
        with pytest.raises(AttributeError):
            sample_chunk.path = Path("other.py")  # type: ignore[misc]

    def test_cannot_set_kind(self, sample_chunk: Chunk) -> None:
        with pytest.raises(AttributeError):
            sample_chunk.kind = "class"  # type: ignore[misc]

    def test_cannot_set_symbol(self, sample_chunk: Chunk) -> None:
        with pytest.raises(AttributeError):
            sample_chunk.symbol = "other"  # type: ignore[misc]

    def test_cannot_set_start_line(self, sample_chunk: Chunk) -> None:
        with pytest.raises(AttributeError):
            sample_chunk.start_line = 99  # type: ignore[misc]


class TestChunkEquality:
    """Verify value-based equality from the dataclass."""

    def test_equal_chunks(self) -> None:
        kwargs = {
            "path": Path("a.py"),
            "text": "pass",
            "kind": "module",
            "language": "python",
            "symbol": None,
            "start_line": 1,
            "end_line": 1,
        }
        assert Chunk(**kwargs) == Chunk(**kwargs)

    def test_different_chunks(self, sample_chunk: Chunk) -> None:
        other = Chunk(
            path=Path("other.py"),
            text="pass",
            kind="module",
            language="python",
            symbol=None,
            start_line=1,
            end_line=1,
        )
        assert sample_chunk != other


class TestChunkSlots:
    """Verify slots — prevent accidental attribute injection."""

    def test_cannot_add_attribute(self, sample_chunk: Chunk) -> None:
        with pytest.raises((AttributeError, TypeError)):
            sample_chunk.extra = "nope"  # type: ignore[attr-defined]
