"""Parse heading-structured prose into sections.

Markdown and AsciiDoc differ only in the character that marks a heading
and the suffixes they use, so one parser serves both. A section runs
from its heading to the line before the next heading at any level, and
its symbol is the path of headings above it.

Both formats also carry fenced code, where a run of ``#`` or ``=`` is
source rather than a heading. Track the fences and ignore what is
inside them.

A file with no heading is one chunk when it holds prose, and nothing
when it holds only machinery. Measured on one specification tree: 2,844
files held no heading, and a sample of 400 of them was attribute
definitions, include shells, and table rows. A paragraph of real prose
with no heading would otherwise disappear with no signal.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ish.adapters.parser._asciidoc import Expander
from ish.adapters.parser._limits import MAX_CHUNK_CHARS
from ish.domain.chunk import Chunk

# A fence is three or more backticks or tildes in Markdown, and four
# hyphens or dots in AsciiDoc.
log = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*(`{3,}|~{3,}|-{4,}|\.{4,}|={4,}\s*$)")

# A line that is machinery rather than prose: an attribute definition,
# an include, a conditional, a comment, a table row or rule, a block
# attribute list, or a heading marker with no title.
_MACHINERY = re.compile(
    r"^\s*(:[\w-]+!?:|include::|ifdef::|ifndef::|ifeval::|endif::|//|<!--|\|"
    r"|\[.*\]\s*$|[#=]+\s*$)"
)


class MarkupParser:
    """Extract one chunk per heading from a prose document."""

    def __init__(
        self,
        language: str,
        suffixes: frozenset[str],
        marker: str,
        expander: Expander | None = None,
    ) -> None:
        self.language = language
        self.suffixes = suffixes
        self._heading = re.compile(rf"^({re.escape(marker)}+)\s+(\S.*)$")
        self._expander = expander
        if expander is not None:
            # Expanded text no longer lines up with the file, so this
            # parser divides a long section itself, between lines of the
            # file, and build_parsers() sets the cap here.
            self.limit = MAX_CHUNK_CHARS

    @classmethod
    def markdown(cls) -> "MarkupParser":
        """Build the Markdown flavor."""
        return cls("markdown", frozenset({".md", ".markdown"}), "#")

    @classmethod
    def asciidoc(cls) -> "MarkupParser":
        """Build the AsciiDoc flavor."""
        return cls(
            "asciidoc", frozenset({".adoc", ".asciidoc", ".asc"}), "=", Expander()
        )

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
                self._pieces(
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

    def _expand(self, path: Path, lines: list[str]) -> list[str]:
        """Return the text each line stands for, as a reader would see it.

        Resolve the includes, and write a figure and a block title as
        text, because ``image::`` markup tells a search nothing.
        """
        if self._expander is None:
            return lines
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

        limit = getattr(self, "limit", None)
        text = "".join(expanded[start - 1 : end])
        if limit is None or len(text) <= limit:
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

    def _whole(self, path: Path, lines: list[str]) -> list[Chunk]:
        """Return the document as one chunk when it holds prose, else nothing.

        Name it after the file, because nothing inside names it. A file
        of attribute definitions, includes, and table rows holds nothing
        a query would ask for, so it stays out as before.
        """
        if not any(_is_prose(line) for _number, line in self._outside_fences(lines)):
            return []
        expanded = self._expand(path, lines)
        return self._pieces(
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


# A table opens and closes on a line of four or more equal signs after a
# pipe. A block title starts with one dot and a word.
_DELIMITER = re.compile(r"^\|={3,}\s*$")
_CLOSE = "|===\n"
_IMAGE = re.compile(r"^image::([^\[]+)\[([^\]]*)\]\s*$")
_TITLE = re.compile(r"^\.([^.\s].*)$")


@dataclass(frozen=True, slots=True)
class _Table:
    """A table in a section: the file lines it spans, its size, and its head.

    A table may open inside an include, so it is found in the expanded
    text, and *opens* and *closes* are the lines of the file whose text
    holds its delimiters.
    """

    opens: int
    closes: int
    size: int
    prefix: str


def _tables(
    expanded: list[str], start: int, end: int, limit: int
) -> tuple[dict[int, _Table], dict[int, _Table]]:
    """Find the tables of lines *start* to *end*, in their expanded text.

    Return the tables by the line that opens each, and, for every line at
    whose end a table is still open, that table. The head of a table is
    its attribute line, the delimiter, and its header row: the rows
    before the first blank line, when the attribute line names a header
    or one row stands before that blank. A head that would fill a
    quarter of a piece is not repeated.
    """
    opened: dict[int, _Table] = {}
    open_at: dict[int, int] = {}
    current: dict | None = None
    previous = ""
    for number in range(start, end + 1):
        for line in expanded[number - 1].splitlines(keepends=True):
            if _DELIMITER.match(line):
                if current is None:
                    current = {
                        "opens": number,
                        "attributes": previous if previous.startswith("[") else "",
                        "named": previous.startswith("[") and "header" in previous,
                        "header": [],
                        "heading": True,
                        "size": len(line),
                    }
                else:
                    current["size"] += len(line)
                    opened[current["opens"]] = _close(current, number, limit)
                    current = None
                previous = line
                continue
            if current is not None:
                current["size"] += len(line)
                if current["heading"]:
                    if line.strip():
                        current["header"].append(line)
                    else:
                        current["heading"] = False
            if line.strip():
                previous = line
        if current is not None:
            open_at[number] = current["opens"]
    if current is not None:
        opened[current["opens"]] = _close(current, end, limit)
    return opened, {number: opened[opens] for number, opens in open_at.items()}


def _close(current: dict, closes: int, limit: int) -> _Table:
    """Freeze what is known of a table into its record."""
    header = current["header"]
    keep = (current["named"] or len(header) == 1) and not current["heading"]
    prefix = current["attributes"] + _CLOSE + ("".join(header) + "\n" if keep else "")
    if len(prefix) > limit // 4:
        prefix = _CLOSE
    return _Table(current["opens"], closes, current["size"], prefix)


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
