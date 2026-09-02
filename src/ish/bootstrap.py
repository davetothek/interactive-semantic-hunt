"""Compose the application — the single place that wires adapters into use cases.

Every interface (CLI, TUI, Python API) builds its object graph through
this module. It is the only module allowed to import both application
code and concrete adapters.
"""

import hashlib
import logging
import os
from collections.abc import Callable
from pathlib import Path

from ish.application.ports.embedder import Embedder
from ish.application.ports.parser import Parser
from ish.application.scan import Scan
from ish.application.search import Search
from ish.settings import Settings

log = logging.getLogger(__name__)


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


def _markdown_parser() -> Parser:
    from ish.adapters.parser.markup import MarkupParser

    return MarkupParser.markdown()


def _asciidoc_parser() -> Parser:
    from ish.adapters.parser.markup import MarkupParser

    return MarkupParser.asciidoc()


def _yaml_parser() -> Parser:
    from ish.adapters.parser.structured import StructuredParser

    return StructuredParser.yaml()


def _json_parser() -> Parser:
    from ish.adapters.parser.structured import StructuredParser

    return StructuredParser.json()


def _cpp_parser() -> Parser:
    from ish.adapters.parser.tree_sitter import cpp_parser

    return cpp_parser()


# Source parsers by language name. Each factory imports lazily so an unused
# grammar adds no startup cost. Register new parsers here only.
PARSERS: dict[str, Callable[[], Parser]] = {
    "python": _python_parser,
    "markdown": _markdown_parser,
    "asciidoc": _asciidoc_parser,
    "cpp": _cpp_parser,
    "yaml": _yaml_parser,
    "json": _json_parser,
}


def all_parsers(settings: Settings) -> dict[str, Callable[[], Parser]]:
    """Return every parser available, built in or written by the user.

    A user parser replaces a built-in one of the same language, which is
    how a project teaches ish about its own dialect of a format.
    """
    if not settings.plugins:
        return dict(PARSERS)

    from ish.adapters.parser.plugins import load_parsers

    return {**PARSERS, **load_parsers()}


def build_parsers(settings: Settings) -> list[Parser]:
    """Return the enabled source parsers.

    Build every registered parser when the ``languages`` option is empty.
    Otherwise build only the languages it names, in that order.
    """
    from ish.application.search import canonical_language

    available = all_parsers(settings)
    # Accept the same spellings the query line accepts, so `--languages c`
    # and `lang:c` name one parser.
    wanted = tuple(canonical_language(name) for name in settings.languages) or tuple(
        available
    )

    unknown = [name for name in wanted if name not in available]
    if unknown:
        valid = ", ".join(sorted(available))
        raise ValueError(
            f"Unknown language(s): {', '.join(unknown)}. Valid languages: {valid}"
        )

    return [available[name]() for name in wanted]


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
    """Return the directory that holds the persistent indexes.

    Use the data directory rather than the cache directory. An index of
    a large tree costs hours to build, and a cache directory may be
    deleted at any time by the system.
    """
    if settings.cache_dir:
        return Path(settings.cache_dir)
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ish"


def index_path(settings: Settings, root: Path) -> Path:
    """Return the index file for one scanned tree.

    Name it after the tree so separate projects never share an index, and
    keep the basename readable for anyone inspecting the cache.
    """
    resolved = root.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return index_dir(settings) / f"{resolved.name}-{digest}.db"


def find_indexes(settings: Settings, path: Path) -> dict[Path, Path]:
    """Return every stored index whose tree sits at or below *path*.

    Read the tree from inside each index, because the file name carries
    only a hash of it.
    """
    from ish.adapters.vector_store.sqlite import SqliteVectorStore

    directory = index_dir(settings)
    if not directory.is_dir():
        return {}

    wanted = path.resolve()
    found: dict[Path, Path] = {}
    for db_path in sorted(directory.glob("*.db")):
        root = SqliteVectorStore.read_root(db_path)
        if root is None:
            continue
        if root == wanted or wanted in root.parents:
            found[root] = db_path
    return found


def build_vector_store(settings: Settings, root: Path, embedder: Embedder):
    """Build the vector store for one scanned tree.

    Persist to disk unless the caller asked for a run that leaves none.
    """
    if settings.no_cache:
        from ish.adapters.vector_store.pure_python import PurePythonVectorStore

        return PurePythonVectorStore()

    from ish.adapters.vector_store.federated import FederatedVectorStore
    from ish.adapters.vector_store.sqlite import SqliteVectorStore

    identity = model_id(settings, embedder)
    resolved = root.resolve()
    existing = find_indexes(settings, resolved) if settings.federate else {}

    def open_index(path: Path, tree: Path) -> SqliteVectorStore:
        return SqliteVectorStore(path, model_id=identity, root=tree)

    # Write to the index of the named tree. Build one only when nothing
    # below already covers the search, so asking about a parent of
    # several indexes reads them rather than starting a new one.
    primary = None
    if resolved in existing or not existing:
        primary = open_index(index_path(settings, resolved), resolved)

    others = [open_index(db, tree) for tree, db in existing.items() if tree != resolved]
    if not others:
        return primary

    log.info("Searching %d indexes under %s", len(others) + bool(primary), resolved)
    return FederatedVectorStore(primary, others)


def build_ignored_by(settings: Settings, root: Path):
    """Return the predicate that skips files a repository ignores."""
    if not settings.git:
        return None

    from ish.adapters.vcs.git import GitVisibleFiles

    return GitVisibleFiles(root).ignores


def build_scan(settings: Settings, root: Path) -> Scan:
    """Wire the scan use case."""
    return Scan(
        parsers=build_parsers(settings),
        ignored_dirs=settings.ignore,
        include=settings.include,
        exclude=settings.exclude,
        ignored_by=build_ignored_by(settings, root),
    )


def build_search(settings: Settings, root: Path) -> Search:
    """Wire the full search use case for one scanned tree."""
    from ish.application.search import build_result_filter

    embedder = build_embedder(settings)
    return Search(
        scan=build_scan(settings, root),
        embedder=embedder,
        vector_store=build_vector_store(settings, root, embedder),
        reindex=settings.reindex,
        hybrid=not settings.no_hybrid,
        keep=build_result_filter(settings_filters(settings)),
    )


def settings_filters(settings: Settings):
    """Return the result filters the configuration asks for."""
    from ish.application.search import Filters

    return Filters(settings.lang, settings.under, settings.type)
