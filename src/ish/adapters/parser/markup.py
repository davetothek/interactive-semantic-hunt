"""Parse heading-structured prose into sections.

Markdown and AsciiDoc differ only in the character that marks a heading
and the suffixes they use, so one parser serves both. A section runs
from its heading to the line before the next heading at any level, and
its symbol is the path of headings above it.

Both formats also carry fenced code, where a run of ``#`` or ``=`` is
source rather than a heading. Track the fences and ignore what is
inside them.
"""

import re
from collections.abc import Sequence
from pathlib import Path

from ish.domain.chunk import Chunk

# A fence is three or more backticks or tildes in Markdown, and four
# hyphens or dots in AsciiDoc.
_FENCE = re.compile(r"^\s*(`{3,}|~{3,}|-{4,}|\.{4,}|={4,}\s*$)")


class MarkupParser:
    """Extract one chunk per heading from a prose document."""

    def __init__(self, language: str, suffixes: frozenset[str], marker: str) -> None:
        self.language = language
        self.suffixes = suffixes
        self._heading = re.compile(rf"^({re.escape(marker)}+)\s+(\S.*)$")

    @classmethod
    def markdown(cls) -> "MarkupParser":
        """Build the Markdown flavor."""
        return cls("markdown", frozenset({".md", ".markdown"}), "#")

    @classmethod
    def asciidoc(cls) -> "MarkupParser":
        """Build the AsciiDoc flavor."""
        return cls("asciidoc", frozenset({".adoc", ".asciidoc", ".asc"}), "=")

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Return one chunk per heading, nested by heading level."""
        lines = source.splitlines(keepends=True)
        headings = self._headings(lines)
        if not headings:
            return []

        chunks: list[Chunk] = []
        trail: list[str] = []
        for index, (start, level, title) in enumerate(headings):
            following = headings[index + 1][0] - 1 if index + 1 < len(headings) else 0
            end = following or len(lines)

            # Keep the ancestors above this level, then add this title.
            del trail[level - 1 :]
            trail.append(title)

            chunks.append(
                Chunk(
                    path=path,
                    text="".join(lines[start - 1 : end]),
                    kind="document" if level == 1 else "section",
                    language=self.language,
                    symbol=" > ".join(trail),
                    start_line=start,
                    end_line=end,
                )
            )
        return chunks

    def _headings(self, lines: list[str]) -> list[tuple[int, int, str]]:
        """Find every heading outside a fenced block."""
        found: list[tuple[int, int, str]] = []
        fence: str | None = None

        for number, line in enumerate(lines, 1):
            fence_match = _FENCE.match(line)
            if fence_match:
                token = fence_match.group(1).strip()
                if fence is None:
                    fence = token[0]
                    continue
                if token[0] == fence:
                    fence = None
                continue
            if fence is not None:
                continue

            heading = self._heading.match(line)
            if heading:
                found.append((number, len(heading.group(1)), heading.group(2).strip()))
        return found
