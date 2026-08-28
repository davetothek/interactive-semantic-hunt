"""Test the Parser protocol contract."""

from collections.abc import Sequence
from pathlib import Path

from ish.application.ports.parser import Parser
from ish.domain.chunk import Chunk


class FakeParser:
    """Minimal implementation that satisfies the Parser protocol."""

    suffixes = frozenset({".py"})

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Return a single dummy chunk for any input."""
        return [
            Chunk(
                path=path,
                text=source,
                kind="module",
                symbol=None,
                start_line=1,
                end_line=1,
            )
        ]


class TestParserProtocol:
    """Verify that the Parser protocol accepts conforming implementations."""

    def test_fake_parser_is_instance(self) -> None:
        """Confirm structural subtyping works at runtime."""
        assert isinstance(FakeParser(), Parser)

    def test_fake_parser_returns_chunks(self) -> None:
        parser: Parser = FakeParser()
        chunks = parser.parse(Path("test.py"), "x = 1")
        assert len(chunks) == 1
        assert chunks[0].kind == "module"

    def test_fake_parser_preserves_path(self) -> None:
        parser: Parser = FakeParser()
        path = Path("src/foo.py")
        chunks = parser.parse(path, "pass")
        assert chunks[0].path == path

    def test_fake_parser_preserves_source(self) -> None:
        parser: Parser = FakeParser()
        source = "def f(): pass"
        chunks = parser.parse(Path("f.py"), source)
        assert chunks[0].text == source
