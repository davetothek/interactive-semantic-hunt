"""Define where a chunk sits in its file, in the unit the file is read in.

A text file is read in lines, a PDF in pages, a deck in slides, a sheet
in cells, and a word-processor document in headed sections, because its
pages move with the fonts and the printer. Each kind renders itself, so
nothing that prints or links a chunk needs to know the kind.
"""

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable


@runtime_checkable
class Location(Protocol):
    """Contract for the place of a chunk inside its file."""

    @property
    def kind(self) -> str:
        """Name of the unit, such as ``lines`` or ``page``."""
        ...

    def render(self) -> str:
        """Return the text that follows the path when a person reads it."""
        ...

    def fragment(self) -> str:
        """Return the URL fragment that opens the file at this place."""
        ...

    def to_dict(self) -> dict[str, object]:
        """Return the place as plain data, for a client that reads JSON."""
        ...


def _span(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


@dataclass(frozen=True, slots=True)
class LineRange:
    """Lines *start* to *end* of a text file, 1-based and inclusive."""

    start: int
    end: int
    kind: ClassVar[str] = "lines"

    def render(self) -> str:
        # Keep both ends, even for one line: the scan format reads
        # `path:start-end`, and an editor client parses exactly that.
        return f":{self.start}-{self.end}"

    def fragment(self) -> str:
        return f"L{_span(self.start, self.end)}"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class PageRange:
    """Pages *start* to *end* of a paginated file, 1-based and inclusive."""

    start: int
    end: int
    kind: ClassVar[str] = "page"

    def render(self) -> str:
        return f"#page={_span(self.start, self.end)}"

    def fragment(self) -> str:
        # RFC 8118 opens a PDF at one page, so link the first.
        return f"page={self.start}"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class SlideRange:
    """Slides *start* to *end* of a deck, 1-based and inclusive."""

    start: int
    end: int
    kind: ClassVar[str] = "slide"

    def render(self) -> str:
        return f"#slide={_span(self.start, self.end)}"

    def fragment(self) -> str:
        return f"slide={self.start}"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class CellRange:
    """A block of cells on one sheet, in A1 notation such as ``A2:F40``."""

    sheet: str
    cells: str
    kind: ClassVar[str] = "cells"

    def render(self) -> str:
        return f"#{self.fragment()}"

    def fragment(self) -> str:
        sheet = self.sheet.replace("'", "''")
        return f"'{sheet}'!{self.cells}"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "sheet": self.sheet, "cells": self.cells}


@dataclass(frozen=True, slots=True)
class Section:
    """A headed section, by its number and its heading text."""

    number: str
    title: str
    kind: ClassVar[str] = "section"

    def render(self) -> str:
        return f"#section={self.number}"

    def fragment(self) -> str:
        return f"section={self.number}"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "number": self.number, "title": self.title}
