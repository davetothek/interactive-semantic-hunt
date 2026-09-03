"""Expose the public Python API for programmatic use.

Offer the same three things the other interfaces offer — search a tree,
list what is indexed, and report what the index holds — without a
process start between calls. A long-lived `Ish` keeps its indexes open,
so a second query costs a search rather than an interpreter.

Import this module rather than the package root: `ish` itself must stay
free of layer imports, and `tests/unit/test_package.py` enforces that.

    from ish.interfaces.python.api import Ish

    with Ish("src/") as ish:
        for chunk, score in ish.search("parse the config", limit=5):
            print(score, chunk.path, chunk.symbol)
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from types import TracebackType

from ish import bootstrap
from ish.application.search import (
    Filters,
    Search,
    parse_query,
)
from ish.domain.chunk import Chunk
from ish.settings import Settings, load_settings

log = logging.getLogger(__name__)

Result = tuple[Chunk, float]


class Ish:
    """Search one tree by meaning.

    Hold the index open for as long as the object lives. Close it with
    `close()`, or use the object as a context manager.
    """

    def __init__(
        self,
        path: str | Path = ".",
        *,
        settings: Settings | None = None,
        **overrides: object,
    ) -> None:
        """Open *path* for searching.

        Resolve settings the way the command line does — defaults, then
        the configuration files, then the environment — unless a caller
        passes its own. Keyword arguments override single options, so a
        script can ask for one embedder without building a whole
        `Settings`.
        """
        self.path = Path(path).expanduser().resolve()
        base = settings if settings is not None else load_settings(start=self.path)
        self.settings = replace(base, **overrides) if overrides else base
        self._search: Search | None = None

    # ------------------------------------------------------------------
    # Lifetime
    # ------------------------------------------------------------------

    def __enter__(self) -> Ish:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the index. Using the object again reopens it."""
        if self._search is not None:
            self._search.close()
            self._search = None

    @property
    def _use_case(self) -> Search:
        """Return the use case, building it on first use."""
        if self._search is None:
            self._search = bootstrap.build_search(self.settings, self.path)
        return self._search

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(self, on_progress: Callable[[str], None] | None = None) -> int:
        """Bring this tree's index up to date. Return the chunks it holds.

        Report progress through *on_progress*, since a first index of a
        large tree runs for minutes.
        """
        chunks = self._use_case.build_index(self.path, on_progress)
        return len(chunks) if chunks else 0

    def refresh_all(
        self, on_progress: Callable[[str], None] | None = None
    ) -> list[Path]:
        """Bring every index at or below this tree up to date.

        A search of a parent reads the indexes beneath it and writes to
        none, so refreshing means visiting each tree in turn.
        """
        self.close()
        return bootstrap.refresh_indexes(self.settings, self.path, on_progress)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int | None = None,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
        hybrid: bool | None = None,
    ) -> list[Result]:
        """Return the best matching chunks, most similar first.

        Read `lang:`, `under:`, and `type:` out of *query* as well as
        from the arguments, so a line typed by a person works unchanged.
        A filter in the query wins over one passed here.
        """
        text, typed = parse_query(query)
        filters = typed.or_else(
            Filters(tuple(lang), under, tuple(type)).or_else(
                bootstrap.settings_filters(self.settings)
            )
        )
        use_case = self._use_case
        if use_case.build_index(self.path) is None:
            return []
        return list(
            use_case.search(
                text,
                limit if limit is not None else self.settings.limit,
                keep=bootstrap.build_result_filter(self.settings, filters),
                hybrid=hybrid,
            )
        )

    def chunks(
        self,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
    ) -> list[Chunk]:
        """Return every indexed chunk the filters allow, unranked."""
        filters = Filters(tuple(lang), under, tuple(type)).or_else(
            bootstrap.settings_filters(self.settings)
        )
        self._use_case.build_index(self.path)
        return self._use_case.all_chunks(
            bootstrap.build_result_filter(self.settings, filters)
        )

    def status(self) -> dict[str, object]:
        """Report what is indexed for this tree.

        Count the chunks by language and by kind, so a caller can see
        what a search can reach without listing all of it.
        """
        chunks = self._use_case.all_chunks()
        sort_into = bootstrap.build_categorizer(self.settings)
        languages: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for chunk in chunks:
            languages[chunk.language] = languages.get(chunk.language, 0) + 1
            kind = sort_into(chunk)
            kinds[kind] = kinds.get(kind, 0) + 1
        return {
            "path": self.path,
            "chunks": len(chunks),
            "files": len({chunk.path for chunk in chunks}),
            "indexes": sorted(bootstrap.find_indexes(self.settings, self.path)),
            "languages": dict(sorted(languages.items())),
            "types": dict(sorted(kinds.items())),
        }

    def __repr__(self) -> str:
        return f"Ish({str(self.path)!r})"
