"""Compose the application — the single place that wires adapters into use cases.

Every interface builds its object graph through ``Ish``, which calls
this module. It is the only module allowed to import both application
code and concrete adapters.

The registries live beside what they register: ``adapters/parser``
lists every parser and ``adapters/embedder`` every backend, each with
the recipe for adding one. This module only selects from them.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ish.adapters.embedder import EMBEDDERS
from ish.adapters.parser import Language, available_parsers, categories, spellings
from ish.adapters.vector_store.catalog import IndexCatalog
from ish.application.categories import Categorizer, compile_categories
from ish.application.filters import Filters
from ish.application.filters import build_result_filter as make_result_filter
from ish.application.index import Index
from ish.application.languages import LanguageResolver, resolve_with
from ish.application.ports.embedder import Embedder
from ish.application.ports.parser import Parser
from ish.application.ports.vector_store import VectorReader, VectorStore
from ish.application.progress import REFRESH, Progress, ProgressCallback
from ish.application.ranking import ResultFilter
from ish.application.scan import Scan
from ish.application.search import Search
from ish.domain.chunk import Chunk
from ish.settings import Settings

log = logging.getLogger(__name__)


def all_parsers(settings: Settings) -> dict[str, Language]:
    """Return every language the settings allow: built in, and the user's own."""
    return available_parsers(settings.plugins)


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """What the registered languages are called, and what they hold.

    Read the registry once and carry the answers, because a long-lived
    interface narrows on every keystroke and the registry may read the
    user's plugin directory to answer.
    """

    resolve: LanguageResolver
    """Turn any spelling of a language into the name it is registered under."""

    categorize: Categorizer
    """Sort a chunk into code, doc, test, or config."""

    spellings: tuple[str, ...] = ()
    """Every name a reader may type for a language, sorted."""


def build_vocabulary(settings: Settings) -> Vocabulary:
    """Read the registry and the configured type rules into one value."""
    parsers = all_parsers(settings)
    names = spellings(parsers)
    return Vocabulary(
        resolve=resolve_with(names),
        categorize=compile_categories(settings.type_patterns, categories(parsers)),
        spellings=tuple(sorted(names)),
    )


def build_parsers(settings: Settings) -> list[Parser]:
    """Return the enabled source parsers.

    Build every registered parser when the ``languages`` option is empty.
    Otherwise build only the languages it names, in that order.
    """
    available = all_parsers(settings)
    # Accept the same spellings the query line accepts, so `--languages c`
    # and `lang:c` name one parser.
    resolve = resolve_with(spellings(available))
    wanted = tuple(resolve(name) for name in settings.languages) or tuple(available)

    unknown = [name for name in wanted if name not in available]
    if unknown:
        valid = ", ".join(sorted(available))
        raise ValueError(
            f"Unknown language(s): {', '.join(unknown)}. Valid languages: {valid}"
        )

    from ish.adapters.parser._limits import SizeLimited

    # Wrap here, so every language and every plugin keeps its chunks
    # inside what the embedding model can read.
    return [SizeLimited(available[name].build()) for name in wanted]


def build_embedder(settings: Settings) -> Embedder:
    """Construct the selected embedding backend."""
    try:
        backend = EMBEDDERS[settings.embedder]
    except KeyError:
        valid = ", ".join(sorted(EMBEDDERS))
        raise ValueError(
            f"Unknown embedder {settings.embedder!r}. Valid backends: {valid}"
        ) from None

    return backend.from_option(settings.model)


def model_id(settings: Settings, embedder: Embedder) -> str:
    """Identify the model that produced a vector.

    Read the identity the adapter reports, so changing a backend default
    invalidates the vectors it produced.
    """
    name = embedder.model_name or "default"
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


def catalog(settings: Settings) -> IndexCatalog:
    """Return the catalog of stored indexes the settings point at."""
    return IndexCatalog(index_dir(settings))


def build_stores(
    settings: Settings, root: Path, embedder: Embedder
) -> tuple[VectorStore | None, VectorReader]:
    """Build what a search of *root* reads, and what a refresh of it writes.

    Return the writable index of the named tree, or None when no index
    may be written, beside the reader a search consults. The reader
    holds the writable index too when there is one, so closing the
    reader releases everything.

    Persist to disk unless the caller asked for a run that leaves none.
    """
    if settings.no_cache:
        from ish.adapters.vector_store.pure_python import PurePythonVectorStore

        store = PurePythonVectorStore()
        return store, store

    from ish.adapters.vector_store.federated import FederatedReader
    from ish.adapters.vector_store.sqlite import SqliteVectorStore

    identity = model_id(settings, embedder)
    resolved = root.resolve()
    indexes = catalog(settings)
    existing = indexes.below(resolved) if settings.federate else {}

    def open_index(path: Path, tree: Path) -> SqliteVectorStore:
        return SqliteVectorStore(path, model_id=identity, root=tree)

    # Write to the index of the named tree. Build one only when nothing
    # below already covers the search, so asking about a parent of
    # several indexes reads them rather than starting a new one.
    primary = None
    if resolved in existing or not existing:
        covering = None if existing else indexes.covering(resolved)
        if covering is not None:
            tree, db_path = covering
            log.info(
                "Reading the index for %s, which already covers %s", tree, resolved
            )
            store = open_index(db_path, tree)
            return store, store
        primary = open_index(indexes.path_for(resolved), resolved)

    others = [open_index(db, tree) for tree, db in existing.items() if tree != resolved]
    if not others:
        # Nothing below, so the named tree's own index is the whole search.
        assert primary is not None
        return primary, primary

    if primary is None and not settings.refresh:
        # Nothing here may be written, so a search reads whatever the
        # indexes below already hold. Say so: a stale answer and a fresh
        # one look the same. Stay quiet when a refresh has just run,
        # which is the very thing the warning asks for.
        log.warning(
            "Reading %d stored indexes under %s without refreshing them. "
            "Pass --refresh to bring them up to date first.",
            len(others),
            resolved,
        )
    readers: list[VectorReader] = [*others]
    if primary is not None:
        readers.insert(0, primary)
    log.info("Searching %d indexes under %s", len(readers), resolved)
    return primary, FederatedReader(readers)


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
    embedder = build_embedder(settings)
    resolved = root.resolve()
    keep = build_result_filter(settings, settings_filters(settings))

    # An index that belongs to a tree above this one holds more than was
    # asked for, so keep the answers inside the path.
    if not settings.no_cache and catalog(settings).covering(resolved) is not None:
        keep = _inside(resolved, keep)

    primary, reader = build_stores(settings, root, embedder)
    index = None
    if primary is not None:
        index = Index(
            scan=build_scan(settings, root),
            embedder=embedder,
            vector_store=primary,
            rebuild=settings.reindex,
        )
    return Search(
        embedder=embedder,
        reader=reader,
        index=index,
        hybrid=not settings.no_hybrid,
        keep=keep,
    )


def _inside(root: Path, keep: ResultFilter) -> ResultFilter:
    """Return a filter that also requires a chunk to sit under *root*."""

    def within(chunk: Chunk) -> bool:
        path = chunk.path
        return (path == root or root in path.parents) and (keep is None or keep(chunk))

    return within


def refresh_indexes(
    settings: Settings,
    root: Path,
    on_progress: ProgressCallback | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> list[Path]:
    """Bring every stored index at or below *root* up to date.

    A search of a parent reads the indexes beneath it and writes to
    none, because choosing one to write to would be wrong. Refreshing
    therefore means visiting each tree in turn. Return the trees
    refreshed, in order.

    Read the configuration beside each tree rather than the one beside
    the parent. An index-scope option decides what belongs in an index,
    so refreshing a tree under the parent's options would prune
    everything those options reject: a tree that git ignores, kept by a
    ``git = false`` of its own, would lose every chunk it holds.
    """
    from dataclasses import replace

    from ish.settings import load_settings

    resolved = root.resolve()
    trees = sorted(catalog(settings).below(resolved)) or [resolved]
    for number, tree in enumerate(trees, start=1):
        log.info("Refreshing the index for %s", tree)
        within = None
        if on_progress is not None:
            within = _placed(on_progress, tree, number, len(trees))
            within(Progress(REFRESH))
        # Each tree writes to its own index, so federation must be off.
        per_tree = replace(
            load_settings(overrides or {}, start=tree),
            federate=False,
            refresh=False,
        )
        search = build_search(per_tree, tree)
        try:
            search.build_index(tree, within)
        finally:
            search.close()
    return trees


def _placed(
    report: ProgressCallback, tree: Path, number: int, count: int
) -> ProgressCallback:
    """Return *report*, told which tree of how many each step belongs to.

    A count of files says nothing about which tree they are in, and a
    refresh walks several.
    """

    def within(step: Progress) -> None:
        report(step.within(tree, number, count))

    return within


def build_result_filter(
    settings: Settings, filters: Filters, vocabulary: Vocabulary | None = None
) -> ResultFilter:
    """Build the result filter from the registry and the configured types.

    Take a *vocabulary* that was read once, so a session narrowing every
    keystroke does not read the registry again.
    """
    known = vocabulary or build_vocabulary(settings)
    return make_result_filter(filters, known.categorize, known.resolve)


def settings_filters(settings: Settings) -> Filters:
    """Return the result filters the configuration asks for."""
    return Filters(settings.lang, settings.under, settings.type)
