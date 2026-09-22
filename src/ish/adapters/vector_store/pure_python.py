"""Pure Python adapter for the VectorStore protocol.

Hold everything in memory. Nothing survives the process, so every run
re-indexes from scratch. Use it for tests and for a run that must leave
no trace on disk.
"""

import math
import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from ish.application.ports.vector_store import FileStamp
from ish.application.ranking import ResultFilter, rank, split_identifier
from ish.domain.chunk import Chunk
from ish.domain.match import Match

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _words(text: str) -> set[str]:
    """Split text into lowercase words, names included."""
    joined = f"{text} {split_identifier(text)}"
    return {word.lower() for word in _WORD_RE.findall(joined)}


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Calculate the cosine similarity between two vectors.

    Return 1.0 for identical vectors, 0.0 for orthogonal, -1.0 for opposite.
    Return 0.0 safely if either vector has a magnitude of 0.
    Raise ``ValueError`` when the vectors have different dimensions.
    """
    dot = sum(a * b for a, b in zip(v1, v2, strict=True))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))

    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0

    return dot / (norm1 * norm2)


class PurePythonVectorStore:
    """In-memory exact nearest-neighbor search using pure Python math.

    Exact for MVP-sized repositories with no external dependency.
    """

    def __init__(self) -> None:
        self._stamps: dict[Path, FileStamp] = {}
        self._chunks: dict[Path, list[tuple[Chunk, str]]] = {}
        self._vectors: dict[str, Sequence[float]] = {}

    # ------------------------------------------------------------------
    # Index maintenance
    # ------------------------------------------------------------------

    def file_stamps(self) -> Mapping[Path, FileStamp]:
        """Return the stamp held for every indexed file."""
        return dict(self._stamps)

    def missing_vectors(self, hashes: Collection[str]) -> set[str]:
        """Return the subset of *hashes* that has no stored vector."""
        return {digest for digest in hashes if digest not in self._vectors}

    def add_vectors(self, vectors: Mapping[str, Sequence[float]]) -> None:
        """Store vectors by content hash."""
        self._vectors.update(vectors)

    def set_file(
        self, path: Path, stamp: FileStamp, chunks: Sequence[tuple[Chunk, str]]
    ) -> None:
        """Replace everything held for *path*."""
        self._stamps[path] = stamp
        self._chunks[path] = list(chunks)

    def remove_files(self, paths: Collection[Path]) -> None:
        """Drop everything held for *paths*."""
        for path in paths:
            self._stamps.pop(path, None)
            self._chunks.pop(path, None)

    def clear(self) -> None:
        """Discard every indexed file. Keep the vectors, which are reusable."""
        self._stamps.clear()
        self._chunks.clear()

    def close(self) -> None:
        """Release nothing. The store lives only in memory."""

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def chunks(self) -> Sequence[Chunk]:
        """Return every chunk the store holds, ordered by path then line."""
        found = [chunk for entries in self._chunks.values() for chunk, _ in entries]
        found.sort(key=lambda c: (str(c.path), c.start_line))
        return found

    def count(self) -> int:
        """Return how many chunks the store holds."""
        return sum(len(entries) for entries in self._chunks.values())

    def indexed_paths(self) -> Sequence[Path]:
        """Return every file the store has read, chunks or none."""
        return sorted(self._stamps)

    def search(
        self,
        query_vector: Sequence[float],
        query_text: str = "",
        limit: int = 5,
        keep: ResultFilter = None,
    ) -> Sequence[Match]:
        """Rank chunks by vector similarity, fused with a lexical order."""
        return rank(
            query_vector,
            query_text,
            limit,
            keep,
            semantic=self._semantic,
            lexical=self._lexical,
        )

    def _semantic(
        self, query_vector: Sequence[float], top: int, keep: ResultFilter = None
    ) -> list[Match]:
        """Return the *top* chunks *keep* allows, most similar first."""
        scored = [
            Match(chunk, cosine_similarity(query_vector, vector))
            for entries in self._chunks.values()
            for chunk, digest in entries
            if (vector := self._vectors.get(digest)) is not None
            and (keep is None or keep(chunk))
        ]
        scored.sort(key=lambda match: match.score, reverse=True)
        return scored[:top]

    def _lexical(
        self, query_text: str, limit: int, keep: ResultFilter = None
    ) -> list[Chunk]:
        """Return the *limit* chunks *keep* allows, most query words first."""
        wanted = _words(query_text)
        if not wanted:
            return []

        scored: list[tuple[int, Chunk]] = []
        for entries in self._chunks.values():
            for chunk, _digest in entries:
                if keep is not None and not keep(chunk):
                    continue
                haystack = _words(f"{chunk.symbol or ''} {chunk.text}")
                overlap = len(wanted & haystack)
                if overlap:
                    scored.append((overlap, chunk))

        scored.sort(key=lambda pair: -pair[0])
        return [chunk for _score, chunk in scored[:limit]]
