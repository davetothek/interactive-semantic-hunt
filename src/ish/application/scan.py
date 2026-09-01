"""Implement the scan use case — discover files, parse, return chunks."""

import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from ish.application.ports.parser import ParseError, Parser
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)


def _compile(patterns: Sequence[str], option: str) -> list[re.Pattern[str]]:
    """Compile path filters, naming the option when one is malformed."""
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(
                f"The {option!r} option has an invalid regular expression "
                f"{pattern!r}: {exc}"
            ) from exc
    return compiled


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
        include: Sequence[str] = (),
        exclude: Sequence[str] = (),
        ignored_by: Callable[[Path], bool] | None = None,
    ) -> None:
        self._ignored_dirs = frozenset(ignored_dirs) or DEFAULT_IGNORED_DIRS
        self._include = _compile(include, "include")
        self._exclude = _compile(exclude, "exclude")
        # A predicate supplied by the caller, so the application never
        # learns how a version control system is asked.
        self._ignored_by = ignored_by
        self._by_suffix: dict[str, Parser] = {}
        for parser in parsers:
            for suffix in parser.suffixes:
                owner = self._by_suffix.get(suffix)
                if owner is not None:
                    raise ValueError(
                        f"The {owner.language!r} and {parser.language!r} parsers "
                        f"both claim {suffix!r}. Set the 'languages' option to "
                        f"enable only one of them."
                    )
                self._by_suffix[suffix] = parser

    def run(self, root: Path) -> Sequence[Chunk]:
        """Discover parseable files under *root*, parse each, return all chunks.

        Report per-file read and parse errors to stderr and continue with
        the next file.
        """
        chunks: list[Chunk] = []
        files = self.discover(root)
        log.info("Found %d source files to scan under %s", len(files), root)

        for source_file in files:
            parsed = self.parse_file(source_file)
            if parsed is not None:
                chunks.extend(parsed)

        log.info("Scan complete: extracted %d chunks", len(chunks))
        return chunks

    def accepts(self, path: Path) -> bool:
        """Return True when this scan would index *path*.

        Answer without touching the filesystem, so a caller can ask
        about a file it cannot currently read. Index pruning asks this
        question, so every rule that decides what to index belongs here
        and nowhere else.
        """
        if path.suffix not in self._by_suffix:
            return False
        if any(part in self._ignored_dirs for part in path.parts):
            return False

        # Search the whole path, so "vendor/" matches at any depth.
        text = path.as_posix()
        if self._include and not any(p.search(text) for p in self._include):
            return False
        if any(p.search(text) for p in self._exclude):
            return False
        return not (self._ignored_by is not None and self._ignored_by(path))

    def parse_file(self, path: Path) -> Sequence[Chunk] | None:
        """Read and parse one discovered file.

        Return None when the file cannot be read or parsed, after
        reporting the reason. The caller skips it and continues.
        """
        log.debug("Parsing %s", path)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("Cannot read %s: %s", path, exc)
            return None

        try:
            return self._by_suffix[path.suffix].parse(path, source)
        except ParseError as exc:
            log.warning("Cannot parse %s: %s", path, exc)
            return None

    def discover(self, root: Path) -> list[Path]:
        """Recursively find parseable files, skipping ignored directories.

        Return the discovered paths sorted, so results stay stable.
        """
        if root.is_file():
            return [root] if self.accepts(root) else []

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
            elif entry.is_file() and self.accepts(entry):
                result.append(entry)
