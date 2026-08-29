"""Implement the scan use case — discover files, parse, return chunks."""

import logging
from collections.abc import Sequence
from pathlib import Path

from ish.application.ports.parser import ParseError, Parser
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

# Directories to skip when the caller names none.
DEFAULT_IGNORED_DIRS = frozenset({".git", ".venv", "venv", "__pycache__"})


class Scan:
    """Orchestrate file discovery and parsing for a directory tree.

    Accept parsers through the constructor and route each discovered
    file to the parser that claims its suffix. Call ``run()`` with a
    root path to get back every chunk found under that path.
    """

    def __init__(
        self,
        *,
        parsers: Sequence[Parser],
        ignored_dirs: Sequence[str] = (),
    ) -> None:
        self._ignored_dirs = frozenset(ignored_dirs) or DEFAULT_IGNORED_DIRS
        self._by_suffix: dict[str, Parser] = {}
        for parser in parsers:
            for suffix in parser.suffixes:
                if suffix in self._by_suffix:
                    raise ValueError(f"Two parsers claim the suffix {suffix!r}")
                self._by_suffix[suffix] = parser

    def run(self, root: Path) -> Sequence[Chunk]:
        """Discover parseable files under *root*, parse each, return all chunks.

        Report per-file read and parse errors to stderr and continue with
        the next file.
        """
        chunks: list[Chunk] = []
        files = self._discover(root)
        log.info("Found %d source files to scan under %s", len(files), root)

        for source_file in files:
            log.debug("Parsing %s", source_file)
            try:
                source = source_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.warning("Cannot read %s: %s", source_file, exc)
                continue
            parser = self._by_suffix[source_file.suffix]
            try:
                chunks.extend(parser.parse(source_file, source))
            except ParseError as exc:
                log.warning("Cannot parse %s: %s", source_file, exc)
                continue

        log.info("Scan complete: extracted %d chunks", len(chunks))
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _discover(self, root: Path) -> list[Path]:
        """Recursively find parseable files, skipping ignored directories."""
        if root.is_file():
            return [root] if root.suffix in self._by_suffix else []

        result: list[Path] = []
        self._walk(root, result)
        result.sort()
        return result

    def _walk(self, directory: Path, result: list[Path]) -> None:
        """Depth-first walk, pruning ignored directory names.

        Skip directory symlinks to prevent cycles and duplicate files.
        """
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if entry.is_symlink():
                    log.debug("Skip directory symlink %s", entry)
                elif entry.name not in self._ignored_dirs:
                    self._walk(entry, result)
            elif entry.is_file() and entry.suffix in self._by_suffix:
                result.append(entry)
