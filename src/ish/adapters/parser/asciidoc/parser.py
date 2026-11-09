"""Read AsciiDoc as sections, with includes resolved.

The section walk of ``_sections.py`` divides the file at its headings.
This parser changes three steps of it. ``_expand`` resolves the
includes and writes figures and block titles as text. ``_blocks`` gives
each large include a chunk of its own. ``_pieces`` divides a long section
between lines of the file and keeps each table piece readable.
"""

import logging
import re
from pathlib import Path

from ish.adapters.parser._limits import MAX_CHUNK_CHARS
from ish.adapters.parser._sections import SectionParser
from ish.adapters.parser.asciidoc._includes import Expander
from ish.adapters.parser.asciidoc._tables import (
    _CLOSE,
    INLINE_CHARS,
    _tables,
)
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

# A delimited block opens and closes on four or more hyphens, dots, or
# equal signs: a listing, a literal, an example. Asciidoctor also reads a
# Markdown fence of backticks or tildes.
FENCE = re.compile(r"^\s*(`{3,}|~{3,}|-{4,}|\.{4,}|={4,}\s*$)")

# A figure, and a block title: one dot and a word.
_IMAGE = re.compile(r"^image::([^\[]+)\[([^\]]*)\]\s*$")
_TITLE = re.compile(r"^\.([^.\s].*)$")


class AsciiDocParser(SectionParser):
    """Extract one chunk per heading, with the includes of the file resolved."""

    def __init__(self) -> None:
        super().__init__(
            "asciidoc", frozenset({".adoc", ".asciidoc", ".asc"}), "=", FENCE
        )
        self._expander = Expander()
        # Expanded text no longer lines up with the file, so this
        # parser divides a long section itself, between lines of the
        # file, and build_parsers() sets the cap here.
        self.limit = MAX_CHUNK_CHARS

    def _blocks(
        self, lines: list[str], expanded: list[str], start: int, end: int
    ) -> list[tuple[int, int]]:
        """Return the lines a large include claims: its table, or its run of lines."""
        large = [
            n
            for n in range(start, end + 1)
            if lines[n - 1].startswith("include::")
            and len(expanded[n - 1]) > INLINE_CHARS
        ]
        if not large:
            return []
        tables, _ = _tables(expanded, start, end, MAX_CHUNK_CHARS)
        spans: list[tuple[int, int]] = []
        for number in large:
            table = next(
                (t for t in tables.values() if t.opens <= number <= t.closes), None
            )
            first, last = (table.opens, table.closes) if table else (number, number)
            if spans and first <= spans[-1][1] + 1:
                spans[-1] = (spans[-1][0], max(spans[-1][1], last))
            else:
                spans.append((first, last))
        return spans

    def _expand(self, path: Path, lines: list[str]) -> list[str]:
        """Return the text each line stands for, as a reader would see it.

        Resolve the includes, and write a figure and a block title as
        text, because ``image::`` markup tells a search nothing.
        """
        return [_as_text(text) for text in self._expander.expand(path, lines)]

    def _pieces(
        self,
        path: Path,
        lines: list[str],
        expanded: list[str],
        start: int,
        end: int,
        *,
        kind: str,
        symbol: str,
    ) -> list[Chunk]:
        """Return lines *start* to *end* as one chunk, or as pieces that fit.

        Divide only between lines of the file, so every piece names lines
        that hold it. Move a break before a table that fits whole rather
        than into it. A table too long for one piece divides between its
        rows, and each later piece opens the table again with its header
        row and each piece closes it, so every row keeps the names of its
        columns. A single line whose includes outrun the cap stands alone,
        cut at the cap: the model reads no further anyway, and a cut by the
        size cap would name lines the file does not have.
        """

        def chunk(first: int, last: int, text: str) -> Chunk:
            return Chunk(
                path=path,
                text=text,
                kind=kind,
                language=self.language,
                symbol=symbol,
                start_line=first,
                end_line=last,
            )

        limit = self.limit
        text = "".join(expanded[start - 1 : end])
        if len(text) <= limit:
            return [chunk(start, end, text)]
        tables, open_at = _tables(expanded, start, end, limit)
        pieces: list[Chunk] = []
        first, held = start, ""
        for number in range(start, end + 1):
            line = expanded[number - 1]
            table = tables.get(number)
            whole = table is not None and table.size <= limit
            if held and whole and len(held) + table.size > limit:
                pieces.append(chunk(first, number - 1, held))
                first, held = number, ""
            inside = open_at.get(number - 1)
            # Leave room for the delimiter that closes a table at a break.
            room = len(_CLOSE) if inside else 0
            if held and len(held) + len(line) + room > limit:
                closing = _CLOSE if inside else ""
                pieces.append(chunk(first, number - 1, held + closing))
                first, held = number, inside.prefix if inside else ""
            held += line
        pieces.append(chunk(first, end, held))
        cut = [p for p in pieces if len(p.text) > limit]
        if cut:
            log.info(
                "%s: %d lines expand past %d characters and are cut there",
                path,
                len(cut),
                limit,
            )
        return [
            chunk(p.start_line, p.end_line, p.text[:limit])
            if len(p.text) > limit
            else p
            for p in pieces
        ]


def _as_text(text: str) -> str:
    """Write each figure as a line of text, and a block title without its dot."""
    out = []
    for line in text.splitlines(keepends=True):
        image = _IMAGE.match(line)
        if image:
            target, attributes = image.groups()
            out.append(f"Figure: {_alt(attributes) or Path(target).name}\n")
            continue
        title = _TITLE.match(line)
        out.append(title.group(1) + "\n" if title else line)
    return "".join(out)


def _alt(attributes: str) -> str:
    """Return the alt text of an image: its first positional value, or ``alt=``."""
    parts = [p.strip() for p in attributes.split(",")]
    for part in parts:
        key, sep, value = part.partition("=")
        if sep and key.strip() == "alt":
            return value.strip().strip('"')
    if parts and parts[0] and "=" not in parts[0]:
        return parts[0].strip('"')
    return ""
