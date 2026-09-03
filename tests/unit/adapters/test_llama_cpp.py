"""Test the llama.cpp embedder adapter."""

from unittest.mock import MagicMock

import pytest

from ish.adapters.embedder.llama_cpp import LlamaCppEmbedder


@pytest.fixture()
def mock_llama_cpp(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Mock huggingface_hub and llama_cpp."""
    mock_hf = MagicMock()
    mock_hf.hf_hub_download.return_value = "/fake/path/model.gguf"

    mock_hf_utils = MagicMock()
    mock_hf_utils_logging = MagicMock()
    mock_hf.utils = mock_hf_utils
    mock_hf.utils.logging = mock_hf_utils_logging

    import sys

    monkeypatch.setitem(sys.modules, "huggingface_hub", mock_hf)
    monkeypatch.setitem(sys.modules, "huggingface_hub.utils", mock_hf_utils)
    monkeypatch.setitem(
        sys.modules, "huggingface_hub.utils.logging", mock_hf_utils_logging
    )

    mock_llama_module = MagicMock()
    mock_llama_class = MagicMock()
    mock_llama_module.Llama = mock_llama_class

    mock_instance = MagicMock()
    mock_llama_class.return_value = mock_instance

    monkeypatch.setitem(sys.modules, "llama_cpp", mock_llama_module)

    return mock_hf, mock_instance


class TestLlamaCppEmbedder:
    """Verify adapter behavior with mocked C++ backend."""

    def test_initialization(self, mock_llama_cpp: tuple[MagicMock, MagicMock]) -> None:
        """Confirm it downloads the model and initializes the engine."""
        mock_hf, mock_instance = mock_llama_cpp

        # This will trigger the downloads and instantiation
        LlamaCppEmbedder(repo_id="fake-repo", filename="fake.gguf")

        mock_hf.hf_hub_download.assert_called_once_with(
            repo_id="fake-repo", filename="fake.gguf"
        )

        # Verify Llama was instantiated with the downloaded path and embedding mode
        import llama_cpp

        llama_cpp.Llama.assert_called_once_with(
            model_path="/fake/path/model.gguf", embedding=True, verbose=False
        )

    def test_embed_empty(self, mock_llama_cpp: tuple[MagicMock, MagicMock]) -> None:
        """Confirm empty input returns empty output without calling the engine."""
        _, mock_instance = mock_llama_cpp
        embedder = LlamaCppEmbedder()

        result = embedder.embed_documents([])

        assert result == []
        mock_instance.create_embedding.assert_not_called()

    def test_embed_texts(self, mock_llama_cpp: tuple[MagicMock, MagicMock]) -> None:
        """Confirm it extracts the embeddings from the OpenAI-style response payload."""
        _, mock_instance = mock_llama_cpp
        embedder = LlamaCppEmbedder()

        # Mock an OpenAI-style embeddings response
        mock_instance.create_embedding.return_value = {
            "object": "list",
            "data": [
                {"object": "embedding", "embedding": [0.1, 0.2], "index": 0},
                {"object": "embedding", "embedding": [0.8, 0.9], "index": 1},
            ],
            "model": "/fake/path/model.gguf",
            "usage": {"prompt_tokens": 12, "total_tokens": 12},
        }

        result = embedder.embed_documents(["hello", "world"])

        mock_instance.create_embedding.assert_called_once_with(
            ["search_document: hello", "search_document: world"]
        )
        assert result == [[0.1, 0.2], [0.8, 0.9]]
