"""Embedder protocol definition.

Satisfied by adapters that convert text into dense vector embeddings.

Documents and queries are separate methods because retrieval models are
often trained with a different task prefix for each. An adapter that has
no such convention may treat them identically.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Contract for generating text embeddings."""

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Convert stored texts into vectors.

        The returned sequence must have the exact same length as *texts*.
        The vector at index `i` corresponds to the text at `texts[i]`.
        """
        ...

    def embed_query(self, text: str) -> Sequence[float]:
        """Convert one search query into a vector.

        Return an empty sequence when the backend produces no vector.
        """
        ...
