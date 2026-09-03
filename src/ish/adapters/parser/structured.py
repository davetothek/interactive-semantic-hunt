"""Parse YAML and JSON documents.

Split a document at the entries it is made of. One vector cannot stand
for several unrelated things, however few bytes they occupy: measured on
a real specification corpus, indexing each test case separately rather
than each file moved top-one retrieval from 30 percent to 90 percent.
Size was never the problem; a single embedding of ten unrelated purposes
was.

Index a document whole when it holds no such entries, and split further
when a piece is still too large for the embedding model to read, since
it drops what it cannot reach without saying so.

Parse rather than merely read, so a malformed document is reported
instead of being embedded as if it were prose.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ish.adapters.parser.limits import MAX_CHUNK_CHARS
from ish.application.ports.parser import ParseError
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)


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
        """Return a chunk for the document, or one for each entry it holds."""
        import yaml

        if not source.strip():
            return []

        try:
            document = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ParseError(str(exc).replace("\n", " ")) from exc

        lines = source.splitlines(keepends=True)
        title = _name_of(document, path)
        whole = self._chunk(path, lines, 1, len(lines), title, "document")

        # safe_load already parsed this text, so compose cannot fail.
        root = yaml.compose(source)

        if not _has_entries(root) and len(source) <= MAX_CHUNK_CHARS:
            return [whole]

        chunks: list[Chunk] = []
        self._split(root, path, lines, [title], chunks)
        log.debug("Split %s into %d chunks", path, len(chunks))
        return chunks or [whole]

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
        *,
        may_split_list: bool = True,
    ) -> None:
        """Emit *node* as one chunk, or its parts when it holds several.

        Stop at the first list of things. Its items are the things this
        document describes; going deeper would scatter each of them
        across its own fields. Go deeper only for a piece too large for
        the embedding model to read.
        """
        start, end = _span(node)
        size = sum(len(line) for line in lines[start - 1 : end])
        symbol = " > ".join(trail)

        children = list(_children(node))
        divisible = (may_split_list and _has_entries(node)) or size > MAX_CHUNK_CHARS
        if not children or not divisible:
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

        # Items of a list are the things themselves, so do not divide
        # them again by the same rule.
        inner = node.node if isinstance(node, _Ranged) else node
        deeper = may_split_list and inner.id != "sequence"

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
                may_split_list=deeper,
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


def _has_entries(node: Any) -> bool:
    """Return True when a node holds, or leads to, a list of things.

    A sequence of mappings is a list of distinct things, and each
    deserves its own vector. A mapping is the attributes of one thing,
    and splitting it would scatter that thing across several vectors.
    Descend a mapping only to reach a list beneath it.
    """
    inner = getattr(node, "node", node)
    if inner is None:
        return False
    if inner.id == "sequence":
        return any(item.id == "mapping" for item in inner.value)
    if inner.id == "mapping":
        return any(_has_entries(value) for _key, value in inner.value)
    return False


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
