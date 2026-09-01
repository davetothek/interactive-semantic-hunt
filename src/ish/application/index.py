"""Implement the index use case — keep the vector store current.

Refresh only what changed. Compare a cheap stamp per file to decide what
to parse, embed only the chunk texts the store has never seen, and drop
files that no longer exist.
"""

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ish.application.ports.embedder import Embedder
from ish.application.ports.vector_store import FileStamp, VectorStore
from ish.application.scan import Scan
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

# Store vectors this many at a time. A first index of a large tree runs
# for minutes, so keep the work already done when a request fails.
EMBED_BATCH = 256


def _still_on_disk(path: Path) -> bool:
    """Return True unless the file is known to be gone.

    Treat a permission error as "cannot tell" and keep the entry, so a
    directory that turns unreadable does not empty the index.
    """
    try:
        path.stat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def embed_text(chunk: Chunk) -> str:
    """Render a chunk as the text to embed.

    Prefix the language, kind, and symbol so the vector carries the
    naming context, not only the body.
    """
    return f"{chunk.language} {chunk.kind} {chunk.symbol}:\n{chunk.text}"


def content_hash(text: str) -> str:
    """Return a stable identity for embedded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IndexStats:
    """Report what one refresh did."""

    files_seen: int = 0
    files_parsed: int = 0
    files_removed: int = 0
    chunks_indexed: int = 0
    vectors_embedded: int = 0

    @property
    def changed(self) -> bool:
        """Return True when the refresh altered the store."""
        return bool(self.files_parsed or self.files_removed)


class Index:
    """Keep a vector store in step with a directory tree."""

    def __init__(
        self,
        *,
        scan: Scan,
        embedder: Embedder,
        vector_store: VectorStore,
    ) -> None:
        self._scanner = scan
        self._embedder = embedder
        self._store = vector_store

    def refresh(self, root: Path) -> IndexStats:
        """Bring the store in step with *root* and report what changed."""
        found = self._stamp_all(self._scanner.discover(root))
        stored = self._store.file_stamps()

        removed = self._prune(found, stored, root)
        stale = [path for path, stamp in found.items() if stored.get(path) != stamp]

        log.info(
            "Index: %d files found, %d stale, %d removed",
            len(found),
            len(stale),
            removed,
        )
        if not stale:
            return IndexStats(files_seen=len(found), files_removed=removed)

        parsed, embedded = self._reindex(stale, found)
        return IndexStats(
            files_seen=len(found),
            files_parsed=len(parsed),
            files_removed=removed,
            chunks_indexed=sum(len(entries) for entries in parsed.values()),
            vectors_embedded=embedded,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stamp_all(self, paths: Sequence[Path]) -> dict[Path, FileStamp]:
        """Stat every discovered file. Skip any that vanished mid-scan."""
        stamps: dict[Path, FileStamp] = {}
        for path in paths:
            try:
                info = path.stat()
            except OSError as exc:
                log.warning("Cannot stat %s: %s", path, exc)
                continue
            stamps[path] = FileStamp(mtime_ns=info.st_mtime_ns, size=info.st_size)
        return stamps

    def _prune(
        self,
        found: Mapping[Path, FileStamp],
        stored: Mapping[Path, FileStamp],
        root: Path,
    ) -> int:
        """Drop indexed files that are gone or no longer wanted.

        Never drop a file merely because this scan did not reach it. An
        unreadable directory, a race, or a narrower root all cause
        absence without meaning deletion, and removing on absence alone
        discards an index that is still valid.

        Leave entries for other trees alone, so indexing one subdirectory
        never discards another.
        """
        orphans = [
            path
            for path in stored
            if path not in found
            and self._within(path, root)
            and (not _still_on_disk(path) or not self._scanner.accepts(path))
        ]
        if orphans:
            log.info("Removing %d files from the index", len(orphans))
            self._store.remove_files(orphans)
        return len(orphans)

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        """Return True when *path* sits inside the scanned tree."""
        return path == root or root in path.parents

    def _reindex(
        self, stale: Sequence[Path], found: dict[Path, FileStamp]
    ) -> tuple[dict[Path, list[tuple[Chunk, str]]], int]:
        """Parse stale files, embed unseen texts, and write the result."""
        parsed: dict[Path, list[tuple[Chunk, str]]] = {}
        texts: dict[str, str] = {}

        for path in stale:
            chunks = self._scanner.parse_file(path)
            if chunks is None:
                continue
            entries: list[tuple[Chunk, str]] = []
            for chunk in chunks:
                text = embed_text(chunk)
                digest = content_hash(text)
                texts[digest] = text
                entries.append((chunk, digest))
            parsed[path] = entries

        embedded = self._embed_missing(texts)

        for path, entries in parsed.items():
            self._store.set_file(path, found[path], entries)

        return parsed, embedded

    def _embed_missing(self, texts: dict[str, str]) -> int:
        """Embed only the texts the store has never seen. Return the count.

        Store each batch as it completes. A first index of a large tree
        takes minutes, and one failed request should not discard every
        vector earned before it.
        """
        missing = self._store.missing_vectors(texts.keys())
        if not missing:
            return 0

        ordered = sorted(missing)
        reused = len(texts) - len(ordered)
        log.info("Embedding %d new chunks (%d reused)", len(ordered), reused)

        done = 0
        for start in range(0, len(ordered), EMBED_BATCH):
            batch = ordered[start : start + EMBED_BATCH]
            vectors = self._embedder.embed_documents([texts[d] for d in batch])
            self._store.add_vectors(dict(zip(batch, vectors, strict=True)))
            done += len(batch)
            if len(ordered) > EMBED_BATCH:
                log.info("  embedded %d of %d", done, len(ordered))
        return done
