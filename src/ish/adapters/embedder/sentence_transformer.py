"""SentenceTransformers adapter for the Embedder protocol."""

from collections.abc import Sequence


class SentenceTransformerEmbedder:
    """Generate embeddings using the sentence-transformers library.

    Downloads the specified model from the Hugging Face Hub on first run
    and caches it locally.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name

        import logging
        import os

        # Silence Hugging Face Hub telemetry and symlink warnings
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        # Silence the unauthenticated warnings
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

        import contextlib

        # Delay the heavy import so the CLI stays snappy if this isn't used.
        with open(os.devnull, "w") as null_file, contextlib.redirect_stderr(null_file):
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode texts into vectors and return them as pure Python floats."""
        if not texts:
            return []

        # .encode() can accept a single string or list of strings.
        # Since we use Sequence, we ensure it's converted to a list.
        text_list = list(texts)

        # sentence-transformers returns a numpy array or torch tensor.
        # We enforce numpy internally to easily convert to list[list[float]].
        embeddings = self._model.encode(text_list, convert_to_numpy=True)

        return embeddings.tolist()
