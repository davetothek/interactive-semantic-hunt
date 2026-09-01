"""Parse YAML and JSON documents.

Index each document whole. On the corpus this was built for, every file
carries the same two top-level keys, so splitting on them would separate
a small header from everything else and buy no precision. The median
document is short enough to embed as one chunk.

Parse rather than merely read, so a malformed document is reported
instead of being embedded as if it were prose.
"""

from collections.abc import Sequence
from pathlib import Path

from ish.application.ports.parser import ParseError
from ish.domain.chunk import Chunk

# Keys whose value names the document, in the order they are preferred.
_TITLE_KEYS = ("title", "name", "description", "purpose")


class StructuredParser:
    """Emit one chunk per YAML or JSON document."""

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
        """Return one chunk for the whole document."""
        import yaml

        if not source.strip():
            return []

        try:
            document = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ParseError(str(exc).replace("\n", " ")) from exc

        lines = source.splitlines()
        return [
            Chunk(
                path=path,
                text=source,
                kind="document",
                language=self.language,
                symbol=_name_of(document, path),
                start_line=1,
                end_line=len(lines),
            )
        ]


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
