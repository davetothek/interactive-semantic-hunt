"""Test the SentenceTransformers embedder adapter."""

from unittest.mock import MagicMock

import pytest

from ish.adapters.embedder.sentence_transformer import SentenceTransformerEmbedder


class FakeNumpyArray:
    """A tiny mock of a numpy array that supports .tolist()."""

    def __init__(self, data: list[list[float]]):
        self.data = data

    def tolist(self) -> list[list[float]]:
        return self.data


@pytest.fixture()
def mock_st(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock the heavy SentenceTransformer class."""
    mock_class = MagicMock()
    # When initialized, return a mock instance
    mock_instance = MagicMock()
    mock_class.return_value = mock_instance

    # Patch it where it's imported (inside the __init__)
    # We patch the module it comes from.
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", mock_class)
    return mock_class


class TestSentenceTransformerEmbedder:
    """Verify adapter behavior with a mocked backend."""

    def test_initialization(self, mock_st: MagicMock) -> None:
        """Confirm it instantiates the correct model."""
        SentenceTransformerEmbedder("my-custom-model")
        mock_st.assert_called_once_with("my-custom-model")

    def test_default_initialization(self, mock_st: MagicMock) -> None:
        """Confirm it defaults to a small, fast model."""
        SentenceTransformerEmbedder()
        mock_st.assert_called_once_with("all-MiniLM-L6-v2")

    def test_embed_empty(self, mock_st: MagicMock) -> None:
        """Confirm empty input returns empty output without calling the model."""
        embedder = SentenceTransformerEmbedder()
        mock_instance = mock_st.return_value

        result = embedder.embed_documents([])

        assert result == []
        mock_instance.encode.assert_not_called()

    def test_embed_texts(self, mock_st: MagicMock) -> None:
        """Confirm it encodes texts and converts the numpy array to lists."""
        embedder = SentenceTransformerEmbedder()
        mock_instance = mock_st.return_value

        # Setup the mock to return our fake numpy array
        fake_embeddings = FakeNumpyArray([[0.1, 0.2], [0.3, 0.4]])
        mock_instance.encode.return_value = fake_embeddings

        result = embedder.embed_documents(["hello", "world"])

        # Verify it passed the list of strings and requested numpy
        mock_instance.encode.assert_called_once_with(
            ["hello", "world"], convert_to_numpy=True
        )

        # Verify the output was cleanly converted to lists of floats
        assert result == [[0.1, 0.2], [0.3, 0.4]]
