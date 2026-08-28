"""Vector Store protocol definition.

Satisfied by adapters that can store chunks and their embeddings,
and perform similarity searches.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ish.domain.chunk import Chunk


@runtime_checkable
class VectorStore(Protocol):
    """Contract for storing and searching vector embeddings."""

    def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        """Store the given chunks and their corresponding vectors."""
        ...

    def search(
        self, query_vector: Sequence[float], limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the *limit* most similar chunks to the *query_vector*.

        Returns a sequence of (Chunk, similarity_score) tuples,
        sorted by score in descending order (highest score first).
        """
        ...
