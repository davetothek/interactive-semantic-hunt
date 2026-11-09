"""Parse heading-structured prose into sections.

A section runs from its heading to the line before the next heading at
any level, and its symbol is the path of headings above it. Markdown and
AsciiDoc share this walk and differ in the character that marks a
heading. ``markdown.py`` builds it as it is. ``asciidoc/`` extends it
with includes, tables, and figures through three hooks: ``_expand``,
``_blocks``, and ``_pieces``.

Both formats also carry fenced code, where a run of ``#`` or ``=`` is
source rather than a heading. Track the fences and ignore what is
inside them.

A file with no heading is one chunk when it holds prose, and nothing
when it holds only machinery. Measured on one specification tree: 2,844
files held no heading, and a sample of 400 of them was attribute
definitions, include shells, and table rows. A paragraph of real prose
with no heading would otherwise disappear with no signal.
"""

import re
from collections.abc import Sequence
from pathlib import Path

from ish.adapters.parser._limits import INCLUDED_KIND
from ish.domain.chunk import Chunk

# A fence is three or more backticks or tildes in Markdown, and four
# hyphens or dots in AsciiDoc.
_FENCE = re.compile(r"^\s*(`{3,}|~{3,}|-{4,}|\.{4,}|={4,}\s*$)")

# A line that is machinery rather than prose: an attribute definition,
# an include, a conditional, a comment, a table row or rule, a block
# attribute list, or a heading marker with no title.
_MACHINERY = re.compile(
    r"^\s*(:[\w-]+!?:|include::|ifdef::|ifndef::|ifeval::|endif::|//|<!--|\|"
    r"|\[.*\]\s*$|[#=]+\s*$)"
)


class SectionParser:
    """Extract one chunk per heading from a prose document."""

    def __init__(self, language: str, suffixes: frozenset[str], marker: str) -> None:
        self.language = language
        self.suffixes = suffixes
        self._heading = re.compile(rf"^({re.escape(marker)}+)\s+(\S.*)$")

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Return one chunk per heading, nested by heading level."""
        lines = source.splitlines(keepends=True)
        headings = self._headings(lines)
        if not headings:
            return self._whole(path, lines)
        expanded = self._expand(path, lines)

        chunks: list[Chunk] = []
        trail: list[str] = []
        for index, (start, level, title) in enumerate(headings):
            following = headings[index + 1][0] - 1 if index + 1 < len(headings) else 0
            end = following or len(lines)

            # Keep the ancestors above this level, then add this title.
            del trail[level - 1 :]
            trail.append(title)

            chunks.extend(
                self._section(
                    path,
                    lines,
                    expanded,
                    start,
                    end,
                    kind="document" if level == 1 else "section",
                    symbol=" > ".join(trail),
                )
            )
        return chunks

    def _section(
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
        """Return a section, with what its large includes bring in apart.

        A section of a few prose lines that includes a register table
        holds far more table than prose once the include resolves, and
        its embedding then stands for the table and not for what the
        prose says. Give each large include, or the table that holds it,
        a chunk of its own on its lines, and keep the section's own text,
        with those includes left as their directives, in another. A
        section with no prose of its own outside them yields the tables
        alone.
        """
        blocks = self._blocks(lines, expanded, start, end)
        if not blocks:
            return self._pieces(
                path, lines, expanded, start, end, kind=kind, symbol=symbol
            )
        held = {n for first, last in blocks for n in range(first, last + 1)}
        own = [
            lines[n - 1] if n in held else expanded[n - 1]
            for n in range(1, len(lines) + 1)
        ]
        prose = any(
            _is_prose(lines[n - 1]) for n in range(start + 1, end + 1) if n not in held
        )
        chunks = (
            self._pieces(path, lines, own, start, end, kind=kind, symbol=symbol)
            if prose
            else []
        )
        for first, last in blocks:
            chunks += self._pieces(
                path,
                lines,
                expanded,
                first,
                last,
                kind=INCLUDED_KIND,
                symbol=f"{symbol} (table)",
            )
        return chunks

    def _blocks(
        self, lines: list[str], expanded: list[str], start: int, end: int
    ) -> list[tuple[int, int]]:
        """Return the spans of lines that get a chunk apart from their section.

        A plain section holds none.
        """
        return []

    def _expand(self, path: Path, lines: list[str]) -> list[str]:
        """Return the text each line stands for. A plain line stands for itself."""
        return lines

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
        """Return lines *start* to *end* as one chunk.

        ``SizeLimited`` divides a chunk that is too long for the model.
        """
        return [
            Chunk(
                path=path,
                text="".join(expanded[start - 1 : end]),
                kind=kind,
                language=self.language,
                symbol=symbol,
                start_line=start,
                end_line=end,
            )
        ]

    def _whole(self, path: Path, lines: list[str]) -> list[Chunk]:
        """Return the document as one chunk when it holds prose, else nothing.

        Name it after the file, because nothing inside names it. A file
        of attribute definitions, includes, and table rows holds nothing
        a query would ask for, so it stays out as before.
        """
        if not any(_is_prose(line) for _number, line in self._outside_fences(lines)):
            return []
        expanded = self._expand(path, lines)
        return self._section(
            path, lines, expanded, 1, len(lines), kind="document", symbol=path.stem
        )

    @staticmethod
    def _outside_fences(lines: list[str]):
        """Yield each numbered line that is not inside a fenced block."""
        fence: str | None = None
        for number, line in enumerate(lines, 1):
            fence_match = _FENCE.match(line)
            if fence_match:
                token = fence_match.group(1).strip()
                if fence is None:
                    fence = token[0]
                elif token[0] == fence:
                    fence = None
                continue
            if fence is None:
                yield number, line

    def _headings(self, lines: list[str]) -> list[tuple[int, int, str]]:
        """Find every heading outside a fenced block."""
        found: list[tuple[int, int, str]] = []
        for number, line in self._outside_fences(lines):
            heading = self._heading.match(line)
            if heading:
                found.append((number, len(heading.group(1)), heading.group(2).strip()))
        return found


def _is_prose(line: str) -> bool:
    """Return True for a line a reader wrote to be read."""
    return bool(line.strip()) and not _MACHINERY.match(line)
