"""Define the Parser port.

The application depends on this protocol. Concrete adapters implement it.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from ish.domain.chunk import Chunk


class ParseError(Exception):
    """Raise when a source file cannot be parsed.

    Adapters wrap their native failure (for example ``SyntaxError``)
    in this type so the application stays free of parser internals.
    """


@runtime_checkable
class Parser(Protocol):
    """Accept source text and produce Chunk objects.

    Any concrete parser adapter must satisfy this interface.
    """

    language: str
    """Name of the language this parser reads. Identifies the parser."""

    suffixes: frozenset[str]
    """File suffixes this parser accepts, with the leading dot."""

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Parse *source* from *path* and return the extracted chunks.

        Raise ``ParseError`` when *source* cannot be parsed.
        """
        ...
