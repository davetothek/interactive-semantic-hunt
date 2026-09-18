"""Every embedding backend ish ships, and how to add one.

A backend turns text into vectors. Subclass ``PrefixingEmbedder``, which
applies the task prefixes a model was trained with and caches recent
queries, and implement two things:

    class MyEmbedder(PrefixingEmbedder):
        def __init__(self, model_name: str = "default-model") -> None:
            super().__init__(model_name)
            ...                              # import the library here, not at top

        @classmethod
        def from_option(cls, model: str) -> "MyEmbedder":
            return cls(model) if model else cls()

        def _embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
            ...                              # one vector per text, in order

``from_option`` reads the ``model`` option the way this backend needs;
an empty option means the backend's default.

To add a backend to ish:

1. Write the class in a new module in this package, beside the others.
2. Add one line to ``EMBEDDERS`` below, keyed by the ``--embedder`` name.

The ``--embedder`` choices derive from the keys. Two things may also
need a line:

- ``pyproject.toml`` — an extra, when the backend needs a package the
  default install does not carry, and a ``[[tool.ty.overrides]]`` entry
  so the type checker accepts the import of a package that may be absent.
- ``prefixes.py`` — the task prefixes, when the model was trained with
  them. They are keyed by model name, because the convention belongs to
  the model and not to the backend serving it.

Import the library inside ``__init__``, never at module scope. An extra
may not be installed, and the CLI turns the ``ModuleNotFoundError`` into
a line that names the extra to install.
"""

from ish.adapters.embedder.llama_cpp import LlamaCppEmbedder
from ish.adapters.embedder.ollama import OllamaEmbedder
from ish.adapters.embedder.prefixes import PrefixingEmbedder
from ish.adapters.embedder.sentence_transformer import SentenceTransformerEmbedder

EMBEDDERS: dict[str, type[PrefixingEmbedder]] = {
    "ollama": OllamaEmbedder,
    "llama.cpp": LlamaCppEmbedder,
    "st": SentenceTransformerEmbedder,
}
"""Embedding backends by option name. Register a new backend here."""
