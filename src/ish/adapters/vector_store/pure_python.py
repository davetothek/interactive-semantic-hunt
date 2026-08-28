"""Pure Python adapter for the VectorStore protocol."""

import math
from collections.abc import Sequence

from ish.domain.chunk import Chunk


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
    """In-memory exact nearest-neighbor search using Pure Python math.

    Highly efficient for MVP-sized repositories (e.g. < 10,000 chunks)
    with zero external dependencies.
    """

    def __init__(self) -> None:
        self._data: list[tuple[Chunk, Sequence[float]]] = []

    def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        """Store the given chunks and their corresponding vectors."""
        if len(chunks) != len(vectors):
            raise ValueError(f"Got {len(chunks)} chunks but {len(vectors)} vectors")

        for chunk, vector in zip(chunks, vectors, strict=True):
            self._data.append((chunk, vector))

    def search(
        self, query_vector: Sequence[float], limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the *limit* most similar chunks to the *query_vector*."""
        if not self._data:
            return []

        results: list[tuple[Chunk, float]] = []
        for chunk, vector in self._data:
            score = cosine_similarity(query_vector, vector)
            results.append((chunk, score))

        # Sort descending (highest similarity first)
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]
