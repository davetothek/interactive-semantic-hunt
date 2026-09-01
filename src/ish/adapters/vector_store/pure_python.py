"""Pure Python adapter for the VectorStore protocol.

Hold everything in memory. Nothing survives the process, so every run
re-indexes from scratch. Use it for tests and for a run that must leave
no trace on disk.
"""

import math
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from ish.application.ports.vector_store import (
    LEXICAL_WEIGHT,
    SEMANTIC_WEIGHT,
    FileStamp,
    fuse_rankings,
    is_code_like,
    split_identifier,
)
from ish.domain.chunk import Chunk

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

    def search(
        self,
        query_vector: Sequence[float],
        query_text: str = "",
        limit: int = 5,
        keep: Callable[[Chunk], bool] | None = None,
    ) -> Sequence[tuple[Chunk, float]]:
        """Rank chunks by vector similarity, fused with a lexical order."""
        results: list[tuple[Chunk, float]] = []
        for entries in self._chunks.values():
            for chunk, digest in entries:
                vector = self._vectors.get(digest)
                if vector is None:
                    continue
                results.append((chunk, cosine_similarity(query_vector, vector)))

        results.sort(key=lambda pair: pair[1], reverse=True)
        if keep is not None:
            results = [pair for pair in results if keep(pair[0])]
        if not query_text or not is_code_like(query_text):
            return results[:limit]

        lexical = self._lexical(query_text, limit=max(limit * 4, 20))
        if keep is not None:
            lexical = [chunk for chunk in lexical if keep(chunk)]
        if not lexical:
            return results[:limit]

        candidates = [chunk for chunk, _ in results[: max(limit * 4, 20)]]
        by_chunk = dict(results)
        fused = fuse_rankings(
            [(candidates, SEMANTIC_WEIGHT), (lexical, LEXICAL_WEIGHT)], limit
        )
        return [(chunk, by_chunk.get(chunk, 0.0)) for chunk in fused]

    def _lexical(self, query_text: str, limit: int) -> list[Chunk]:
        """Rank chunks by how many query words they contain."""
        wanted = _words(query_text)
        if not wanted:
            return []

        scored: list[tuple[int, Chunk]] = []
        for entries in self._chunks.values():
            for chunk, _digest in entries:
                haystack = _words(f"{chunk.symbol or ''} {chunk.text}")
                overlap = len(wanted & haystack)
                if overlap:
                    scored.append((overlap, chunk))

        scored.sort(key=lambda pair: -pair[0])
        return [chunk for _score, chunk in scored[:limit]]
