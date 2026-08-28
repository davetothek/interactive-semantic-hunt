"""Test the Embedder protocol contract."""

from collections.abc import Sequence

from ish.application.ports.embedder import Embedder


class FakeEmbedder:
    """A stub embedder that returns a fixed vector for each input text."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return a [1.0, 0.0] vector for every text."""
        return [[1.0, 0.0] for _ in texts]


class TestEmbedderProtocol:
    """Verify that concrete implementations satisfy the Embedder protocol."""

    def test_fake_embedder_is_instance(self) -> None:
        """Confirm runtime_checkable works."""
        assert isinstance(FakeEmbedder(), Embedder)

    def test_fake_embedder_returns_sequence(self) -> None:
        """Confirm the fake behaves correctly."""
        embedder = FakeEmbedder()
        result = embedder.embed(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [1.0, 0.0]
        assert result[1] == [1.0, 0.0]
