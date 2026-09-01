"""Compose the application — the single place that wires adapters into use cases.

Every interface (CLI, TUI, Python API) builds its object graph through
this module. It is the only module allowed to import both application
code and concrete adapters.
"""

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

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


def _python_parser() -> Parser:
    from ish.adapters.parser.python import PythonParser

    return PythonParser()


# Source parsers by language name. Each factory imports lazily so an unused
# grammar adds no startup cost. Register new parsers here only.
PARSERS: dict[str, Callable[[], Parser]] = {
    "python": _python_parser,
}


def build_parsers(settings: Settings) -> list[Parser]:
    """Return the enabled source parsers.

    Build every registered parser when the ``languages`` option is empty.
    Otherwise build only the languages it names, in that order.
    """
    wanted = settings.languages or tuple(PARSERS)

    unknown = [name for name in wanted if name not in PARSERS]
    if unknown:
        valid = ", ".join(sorted(PARSERS))
        raise ValueError(
            f"Unknown language(s): {', '.join(unknown)}. Valid languages: {valid}"
        )

    return [PARSERS[name]() for name in wanted]


def build_embedder(settings: Settings) -> Embedder:
    """Construct the selected embedding backend."""
    try:
        factory = EMBEDDERS[settings.embedder]
    except KeyError:
        valid = ", ".join(sorted(EMBEDDERS))
        raise ValueError(
            f"Unknown embedder {settings.embedder!r}. Valid backends: {valid}"
        ) from None

    return factory(settings.model)


def model_id(settings: Settings, embedder: Embedder) -> str:
    """Identify the model that produced a vector.

    Read the identity the adapter reports, so changing a backend default
    invalidates the vectors it produced.
    """
    name = getattr(embedder, "model_name", "") or "default"
    return f"{settings.embedder}:{name}"


def index_dir(settings: Settings) -> Path:
    """Return the directory that holds the persistent indexes."""
    if settings.cache_dir:
        return Path(settings.cache_dir)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ish"


def index_path(settings: Settings, root: Path) -> Path:
    """Return the index file for one scanned tree.

    Name it after the tree so separate projects never share an index, and
    keep the basename readable for anyone inspecting the cache.
    """
    resolved = root.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return index_dir(settings) / f"{resolved.name}-{digest}.db"


def build_vector_store(settings: Settings, root: Path, embedder: Embedder):
    """Build the vector store for one scanned tree.

    Persist to disk unless the caller asked for a run that leaves none.
    """
    if settings.no_cache:
        from ish.adapters.vector_store.pure_python import PurePythonVectorStore

        return PurePythonVectorStore()

    from ish.adapters.vector_store.sqlite import SqliteVectorStore

    return SqliteVectorStore(
        index_path(settings, root), model_id=model_id(settings, embedder)
    )


def build_scan(settings: Settings) -> Scan:
    """Wire the scan use case."""
    return Scan(parsers=build_parsers(settings), ignored_dirs=settings.ignore)


def build_search(settings: Settings, root: Path) -> Search:
    """Wire the full search use case for one scanned tree."""
    embedder = build_embedder(settings)
    return Search(
        parsers=build_parsers(settings),
        embedder=embedder,
        vector_store=build_vector_store(settings, root, embedder),
        ignored_dirs=settings.ignore,
        reindex=settings.reindex,
    )
