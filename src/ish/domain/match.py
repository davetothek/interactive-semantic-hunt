"""Define the Match value — one chunk and how well it answered a query."""

from typing import NamedTuple

from ish.domain.chunk import Chunk


class Match(NamedTuple):
    """Pair a chunk with its similarity to the query.

    Keep the tuple shape, so ``for chunk, score in results`` reads the
    same everywhere and a pair converts to a mapping with ``dict()``.
    """

    chunk: Chunk
    score: float
    """Cosine similarity between the query and the chunk, in [-1, 1]."""
