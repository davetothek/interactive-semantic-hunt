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
from ish.settings import Settings


def _llama_cpp_embedder(model: str) -> Embedder:
    from ish.adapters.embedder.llama_cpp import LlamaCppEmbedder

    if model:
        repo_id, _, filename = model.rpartition("/")
        return LlamaCppEmbedder(repo_id=repo_id, filename=filename)
    return LlamaCppEmbedder()


def _sentence_transformer_embedder(model: str) -> Embedder:
    from ish.adapters.embedder.sentence_transformer import (
        SentenceTransformerEmbedder,
    )

    if model:
        return SentenceTransformerEmbedder(model_name=model)
    return SentenceTransformerEmbedder()


def _ollama_embedder(model: str) -> Embedder:
    from ish.adapters.embedder.ollama import OllamaEmbedder

    if model:
        return OllamaEmbedder(model_name=model)
    return OllamaEmbedder()


# Embedding backends by option name. Each factory imports lazily so unused
# backends add no startup cost. Register new backends here only.
EMBEDDERS: dict[str, Callable[[str], Embedder]] = {
    "llama.cpp": _llama_cpp_embedder,
    "st": _sentence_transformer_embedder,
    "ollama": _ollama_embedder,
}


def build_parsers(settings: Settings) -> list[Parser]:
    """Return every registered source parser. Register new parsers here only."""
    from ish.adapters.parser.python import PythonParser

    return [PythonParser()]


def build_embedder(settings: Settings) -> Embedder:
    """Construct the selected embedding backend, wrapped in a disk cache."""
    from ish.adapters.embedder.cached import CachedEmbedder

    try:
        factory = EMBEDDERS[settings.embedder]
    except KeyError:
        valid = ", ".join(sorted(EMBEDDERS))
        raise ValueError(
            f"Unknown embedder {settings.embedder!r}. Valid backends: {valid}"
        ) from None

    return CachedEmbedder(factory(settings.model), cache_dir=settings.cache_dir or None)


def build_scan(settings: Settings) -> Scan:
    """Wire the scan use case."""
    return Scan(parsers=build_parsers(settings), ignored_dirs=settings.ignore)


def build_search(settings: Settings) -> Search:
    """Wire the full search use case for the selected embedder."""
    from ish.adapters.vector_store.pure_python import PurePythonVectorStore

    return Search(
        parsers=build_parsers(settings),
        embedder=build_embedder(settings),
        vector_store=PurePythonVectorStore(),
        ignored_dirs=settings.ignore,
    )
