"""llama.cpp adapter for the Embedder protocol."""

from collections.abc import Sequence

from ish.adapters.embedder.prefixes import PrefixingEmbedder


class LlamaCppEmbedder(PrefixingEmbedder):
    """Generate embeddings using a local GGUF model via llama.cpp.

    Automatically downloads `nomic-embed-text` from Hugging Face if not present.
    """

    def __init__(
        self,
        repo_id: str = "nomic-ai/nomic-embed-text-v1.5-GGUF",
        filename: str = "nomic-embed-text-v1.5.Q4_K_M.gguf",
    ) -> None:
        # Expose the model identity for cache keying.
        self.model_name = f"{repo_id}/{filename}"

        import os

        from huggingface_hub.utils.logging import set_verbosity_error

        # Suppress huggingface_hub network warnings
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        set_verbosity_error()

        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama

        # 1. Download/find the model on disk
        model_path = hf_hub_download(repo_id=repo_id, filename=filename)

        # 2. Instantiate the engine. verbose=False hides the massive C++ startup logs.
        self._model = Llama(model_path=model_path, embedding=True, verbose=False)

    def _embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode texts into vectors via llama.cpp."""
        text_list = list(texts)

        # create_embedding accepts a single string or a list of strings
        result = self._model.create_embedding(text_list)

        from typing import cast

        embeddings = [item["embedding"] for item in result["data"]]
        return cast("Sequence[Sequence[float]]", embeddings)
