"""Parse YAML and JSON documents.

Index a document whole while it fits. On the corpus this was built for,
every file carries the same two top-level keys, so splitting them apart
would separate a small header from everything else and buy no precision,
and the median document is short enough to embed as one chunk.

Split only what does not fit. An embedding model reads a fixed number of
tokens and silently drops the rest, so a large document indexed whole is
mostly unsearchable with nothing to say so. Descend into the structure
just far enough that every piece is within reach.

Parse rather than merely read, so a malformed document is reported
instead of being embedded as if it were prose.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ish.application.ports.parser import ParseError
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

# Roughly the text an 8192-token context holds, with room to spare.
# Beyond this an embedding carries only the opening of the document.
MAX_CHUNK_CHARS = 20_000

# Keys whose value names the document, in the order they are preferred.
_TITLE_KEYS = ("title", "name", "description", "purpose")


class StructuredParser:
    """Emit one chunk per YAML or JSON document, or per part of a large one."""

    def __init__(self, language: str, suffixes: frozenset[str]) -> None:
        self.language = language
        self.suffixes = suffixes

    @classmethod
    def yaml(cls) -> StructuredParser:
        """Build the YAML flavor."""
        return cls("yaml", frozenset({".yaml", ".yml"}))

    @classmethod
    def json(cls) -> StructuredParser:
        """Build the JSON flavor. YAML is a superset, so one reader serves."""
        return cls("json", frozenset({".json"}))

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Return one chunk for the document, or several when it is large."""
        import yaml

        if not source.strip():
            return []

        try:
            document = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ParseError(str(exc).replace("\n", " ")) from exc

        lines = source.splitlines(keepends=True)
        title = _name_of(document, path)

        if len(source) <= MAX_CHUNK_CHARS:
            return [self._chunk(path, lines, 1, len(lines), title, "document")]

        # safe_load already parsed this text, so compose cannot fail.
        root = yaml.compose(source)

        chunks: list[Chunk] = []
        self._split(root, path, lines, [title], chunks)
        log.debug("Split %s into %d chunks", path, len(chunks))
        return chunks or [self._chunk(path, lines, 1, len(lines), title, "document")]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chunk(
        self,
        path: Path,
        lines: list[str],
        start: int,
        end: int,
        symbol: str,
        kind: str,
    ) -> Chunk:
        """Build one chunk from a line range."""
        return Chunk(
            path=path,
            text="".join(lines[start - 1 : end]),
            kind=kind,
            language=self.language,
            symbol=symbol,
            start_line=start,
            end_line=end,
        )

    def _split(
        self,
        node: Any,
        path: Path,
        lines: list[str],
        trail: list[str],
        chunks: list[Chunk],
    ) -> None:
        """Emit *node* as one chunk, or its parts when it is too large."""
        start, end = _span(node)
        size = sum(len(line) for line in lines[start - 1 : end])
        symbol = " > ".join(trail)

        children = list(_children(node))
        if size <= MAX_CHUNK_CHARS or not children:
            if size > MAX_CHUNK_CHARS:
                log.warning(
                    "%s: %s holds %d characters and cannot be split further. "
                    "Only its opening will be searchable.",
                    path,
                    symbol,
                    size,
                )
            kind = "document" if len(trail) == 1 else "section"
            chunks.append(self._chunk(path, lines, start, end, symbol, kind))
            return

        for index, (name, child, child_start) in enumerate(children):
            # Give the first part the line that opened the container, so
            # the key naming it is not lost between the pieces.
            begin = start if index == 0 else child_start
            self._split(
                _Ranged(child, begin, _span(child)[1]),
                path,
                lines,
                [*trail, name],
                chunks,
            )


class _Ranged:
    """A node carried with the line range its key opens."""

    def __init__(self, node: Any, start: int, end: int) -> None:
        self.node = node
        self.start = start
        self.end = end


def _span(node: Any) -> tuple[int, int]:
    """Return the 1-based inclusive line range a node covers."""
    if isinstance(node, _Ranged):
        return node.start, node.end
    return node.start_mark.line + 1, max(node.start_mark.line + 1, node.end_mark.line)


def _children(node: Any) -> Sequence[tuple[str, Any, int]]:
    """Return the named parts of a node, with the line each one starts on."""
    inner = node.node if isinstance(node, _Ranged) else node
    if inner.id == "mapping":
        return [
            (str(key.value), value, key.start_mark.line + 1)
            for key, value in inner.value
        ]
    if inner.id == "sequence":
        return [
            (_item_name(item, index), item, item.start_mark.line + 1)
            for index, item in enumerate(inner.value)
        ]
    return []


def _item_name(item: Any, index: int) -> str:
    """Name a sequence entry from its own contents when it says so."""
    if item.id == "mapping":
        for key, value in item.value:
            if str(key.value) in _TITLE_KEYS and value.id == "scalar":
                text = str(value.value).strip()
                if text:
                    return text[:80]
    return f"[{index}]"


def _name_of(document: object, path: Path) -> str:
    """Name the document from its own contents when it says so.

    Fall back to the file stem, which is what a reader would call it.
    """
    if isinstance(document, dict):
        for key in _TITLE_KEYS:
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        metadata = document.get("metadata")
        if isinstance(metadata, dict):
            for key in _TITLE_KEYS:
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return path.stem
