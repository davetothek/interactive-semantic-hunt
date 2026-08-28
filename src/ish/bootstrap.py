"""Compose the application — the single place that wires adapters into use cases.

Every interface (CLI, TUI, Python API) builds its object graph through
this module. It is the only module allowed to import both application
code and concrete adapters.
"""

from collections.abc import Callable

from ish.application.ports.embedder import Embedder
from ish.application.ports.parser import Parser
from ish.application.scan import Scan
from ish.application.search import Search


def _llama_cpp_embedder() -> Embedder:
    from ish.adapters.embedder.llama_cpp import LlamaCppEmbedder

    return LlamaCppEmbedder()


def _sentence_transformer_embedder() -> Embedder:
    from ish.adapters.embedder.sentence_transformer import (
        SentenceTransformerEmbedder,
    )

    return SentenceTransformerEmbedder()


def _ollama_embedder() -> Embedder:
    from ish.adapters.embedder.ollama import OllamaEmbedder

    return OllamaEmbedder()


# Embedding backends by CLI name. Each factory imports lazily so unused
# backends add no startup cost. Register new backends here only.
EMBEDDERS: dict[str, Callable[[], Embedder]] = {
    "llama.cpp": _llama_cpp_embedder,
    "st": _sentence_transformer_embedder,
    "ollama": _ollama_embedder,
}

DEFAULT_EMBEDDER = "llama.cpp"


def build_parsers() -> list[Parser]:
    """Return every registered source parser. Register new parsers here only."""
    from ish.adapters.parser.python import PythonParser

    return [PythonParser()]


def build_embedder(name: str) -> Embedder:
    """Construct the named embedding backend, wrapped in a disk cache."""
    from ish.adapters.embedder.cached import CachedEmbedder

    return CachedEmbedder(EMBEDDERS[name]())


def build_scan() -> Scan:
    """Wire the scan use case."""
    return Scan(parsers=build_parsers())


def build_search(embedder_name: str) -> Search:
    """Wire the full search use case for the selected embedder."""
    from ish.adapters.vector_store.pure_python import PurePythonVectorStore

    return Search(
        parsers=build_parsers(),
        embedder=build_embedder(embedder_name),
        vector_store=PurePythonVectorStore(),
    )
