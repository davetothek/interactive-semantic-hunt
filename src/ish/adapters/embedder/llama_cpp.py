"""llama.cpp adapter for the Embedder protocol.

Run the model in this process. The package is an extra, so import it
only when the backend is built, and let the composition root turn a
missing module into a line that names the extra to install.
"""

from collections.abc import Sequence

from ish.adapters.embedder.hub import quiet_hub
from ish.adapters.embedder.prefixes import PrefixingEmbedder

DEFAULT_REPO = "nomic-ai/nomic-embed-text-v1.5-GGUF"
DEFAULT_FILE = "nomic-embed-text-v1.5.Q4_K_M.gguf"


class LlamaCppEmbedder(PrefixingEmbedder):
    """Generate embeddings with a local GGUF model through llama.cpp.

    Fetch the model from the Hugging Face hub when it is not on disk.
    """

    def __init__(
        self,
        repo_id: str = DEFAULT_REPO,
        filename: str = DEFAULT_FILE,
        context_tokens: int | None = None,
    ) -> None:
        super().__init__(f"{repo_id}/{filename}")
        quiet_hub()

        from huggingface_hub import hf_hub_download
        from llama_cpp import Llama

        model_path = hf_hub_download(repo_id=repo_id, filename=filename)
        # The window is set when the model loads, so it is taken here.
        # None leaves the engine's own default.
        window = {} if context_tokens is None else {"n_ctx": context_tokens}
        # verbose=False keeps the engine's start-up log off the terminal.
        self._model = Llama(
            model_path=model_path, embedding=True, verbose=False, **window
        )

    @classmethod
    def from_option(
        cls, model: str, context_tokens: int | None = None
    ) -> "LlamaCppEmbedder":
        """Build the backend the ``model`` option names.

        Read the option as ``repo/id/filename.gguf``: everything up to
        the last slash names the Hugging Face repository, the rest the
        file in it. Empty means the default model.
        """
        if not model:
            return cls(context_tokens=context_tokens)
        repo_id, _, filename = model.rpartition("/")
        return cls(repo_id=repo_id, filename=filename, context_tokens=context_tokens)

    def _embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode texts into vectors."""
        result = self._model.create_embedding(list(texts))
        return [item["embedding"] for item in result["data"]]
