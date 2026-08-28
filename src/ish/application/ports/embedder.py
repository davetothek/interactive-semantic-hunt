"""Embedder protocol definition.

Satisfied by adapters that convert text into dense vector embeddings.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Contract for generating text embeddings."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Convert a sequence of strings into a sequence of vector embeddings.

        The returned sequence must have the exact same length as *texts*.
        The vector at index `i` corresponds to the text at `texts[i]`.
        """
        ...
