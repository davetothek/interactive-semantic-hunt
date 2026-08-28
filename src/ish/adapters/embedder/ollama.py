"""Ollama adapter for the Embedder protocol."""

from collections.abc import Sequence


class OllamaEmbedder:
    """Generate embeddings using a local Ollama daemon.

    Requires the Ollama service to be running on the host machine.
    """

    def __init__(self, model_name: str = "nomic-embed-text") -> None:
        self.model_name = model_name

        # Defer import to keep CLI fast when unused
        import ollama

        self._client = ollama.Client()

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode texts into vectors via the Ollama REST API."""
        if not texts:
            return []

        # Ollama expects either a single string or a list of strings
        # Sequence must be converted to list for safety
        text_list = list(texts)

        # Note: ollama.embeddings() processes one string.
        # For batch embedding in modern Ollama, we use embed() endpoint
        response = self._client.embed(
            model=self.model_name,
            input=text_list,
        )

        # Returns a list of vectors corresponding to each input string
        return response.get("embeddings", [])
