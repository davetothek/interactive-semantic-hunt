"""Test the Embedder protocol contract."""

from collections.abc import Sequence

from ish.application.ports.embedder import Embedder


class FakeEmbedder:
    """A stub embedder that returns a fixed vector for each input text."""

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return a [1.0, 0.0] vector for every text."""
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> Sequence[float]:
        """Return a fixed vector for one query."""
        return [0.0, 1.0]


class TestEmbedderProtocol:
    """Verify that concrete implementations satisfy the Embedder protocol."""

    def test_fake_embedder_is_instance(self) -> None:
        """Confirm runtime_checkable works."""
        assert isinstance(FakeEmbedder(), Embedder)

    def test_documents_return_one_vector_each(self) -> None:
        result = FakeEmbedder().embed_documents(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [1.0, 0.0]

    def test_query_returns_one_vector(self) -> None:
        assert FakeEmbedder().embed_query("hello") == [0.0, 1.0]

    def test_shipped_adapters_satisfy_the_port(self) -> None:
        """Guard every backend against drifting from the contract."""
        from ish.adapters.embedder.ollama import OllamaEmbedder

        assert isinstance(OllamaEmbedder(), Embedder)
