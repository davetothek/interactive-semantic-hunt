"""Define the Chunk value object — the sole domain model."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Chunk:
    """Represent one named code region extracted from a source file.

    Freeze the instance so domain objects stay immutable across layers.
    Use slots to keep memory tight when scanning large trees.

    Attributes:
        path: Absolute path to the source file that contains this chunk.
        text: Raw source text of the chunk, including leading indentation.
        kind: Structural category (e.g. "function", "class", "method").
        language: Source language of the chunk (e.g. "python", "asciidoc").
        symbol: Qualified name of the definition, or None for anonymous chunks.
        start_line: First line of the chunk in the source file (1-based).
        end_line: Last line of the chunk in the source file (1-based, inclusive).
    """

    path: Path
    text: str
    kind: str
    language: str
    symbol: str | None
    start_line: int
    end_line: int
