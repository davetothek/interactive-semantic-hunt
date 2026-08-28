"""Test the VectorStore protocol contract."""

from collections.abc import Sequence

from ish.application.ports.vector_store import VectorStore
from ish.domain.chunk import Chunk


class FakeVectorStore:
    """A stub vector store."""

    def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        pass

    def search(
        self, query_vector: Sequence[float], limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        return []


class TestVectorStoreProtocol:
    """Verify that concrete implementations satisfy the VectorStore protocol."""

    def test_fake_vector_store_is_instance(self) -> None:
        """Confirm runtime_checkable works."""
        assert isinstance(FakeVectorStore(), VectorStore)
