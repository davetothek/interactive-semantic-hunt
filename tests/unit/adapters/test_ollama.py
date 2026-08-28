"""Test the Ollama embedder adapter."""

from unittest.mock import MagicMock

import pytest

from ish.adapters.embedder.ollama import OllamaEmbedder


@pytest.fixture()
def mock_ollama(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock the official ollama Client."""
    mock_module = MagicMock()
    mock_client_class = MagicMock()
    mock_module.Client = mock_client_class

    mock_instance = MagicMock()
    mock_client_class.return_value = mock_instance

    import sys

    monkeypatch.setitem(sys.modules, "ollama", mock_module)
    return mock_instance


class TestOllamaEmbedder:
    """Verify adapter behavior with a mocked Ollama daemon."""

    def test_initialization(self, mock_ollama: MagicMock) -> None:
        """Confirm it saves the model name and instantiates the client."""
        embedder = OllamaEmbedder("llama3")
        assert embedder.model_name == "llama3"

    def test_default_initialization(self, mock_ollama: MagicMock) -> None:
        """Confirm it defaults to nomic-embed-text."""
        embedder = OllamaEmbedder()
        assert embedder.model_name == "nomic-embed-text"

    def test_embed_empty(self, mock_ollama: MagicMock) -> None:
        """Confirm empty input returns empty output without calling the daemon."""
        embedder = OllamaEmbedder()

        result = embedder.embed([])

        assert result == []
        mock_ollama.embed.assert_not_called()

    def test_embed_texts(self, mock_ollama: MagicMock) -> None:
        """Confirm it sends texts to the daemon and extracts embeddings."""
        embedder = OllamaEmbedder("mxbai-embed-large")

        # Setup the mock response payload
        mock_ollama.embed.return_value = {
            "model": "mxbai-embed-large",
            "embeddings": [[0.5, 0.6], [0.7, 0.8]],
            "total_duration": 1234,
            "load_duration": 12,
            "prompt_eval_count": 5,
        }

        result = embedder.embed(["test1", "test2"])

        # Verify it hit the correct API endpoint with the right payload
        mock_ollama.embed.assert_called_once_with(
            model="mxbai-embed-large", input=["test1", "test2"]
        )

        # Verify the output was cleanly extracted
        assert result == [[0.5, 0.6], [0.7, 0.8]]
