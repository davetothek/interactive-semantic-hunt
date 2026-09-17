"""sentence-transformers adapter for the Embedder protocol.

Run the model in this process. The package is an extra, so import it
only when the backend is built, and let the composition root turn a
missing module into a line that names the extra to install.
"""

import contextlib
import os
from collections.abc import Sequence

from ish.adapters.embedder.hub import quiet_hub
from ish.adapters.embedder.prefixes import PrefixingEmbedder

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class SentenceTransformerEmbedder(PrefixingEmbedder):
    """Generate embeddings with the sentence-transformers library.

    Fetch the model from the Hugging Face hub on the first run.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        super().__init__(model_name)
        quiet_hub()
        # A forked tokenizer warns about parallelism on every run.
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        # The import itself writes to stderr, which a picker is drawing on.
        with open(os.devnull, "w") as null_file, contextlib.redirect_stderr(null_file):
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)

    @classmethod
    def from_option(cls, model: str) -> "SentenceTransformerEmbedder":
        """Build the backend the ``model`` option names. Empty means the default."""
        return cls(model) if model else cls()

    def _embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode texts into vectors and return them as plain floats."""
        return self._model.encode(list(texts), convert_to_numpy=True).tolist()
