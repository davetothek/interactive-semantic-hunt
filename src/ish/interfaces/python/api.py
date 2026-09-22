"""Expose the public Python API, and the session every interface shares.

Offer the same things every interface offers — search a tree, list what
is indexed, scan what is on disk, and report what the index holds —
without a process start between calls. A long-lived `Ish` keeps its
index open, so a second query costs a search rather than an interpreter.

The CLI, the TUI, and the MCP server are thin skins over this class.
Filter precedence, index lifetime, and the join between settings and
use cases live here once, so the four interfaces cannot drift.

Import this module rather than the package root: `ish` itself must stay
free of layer imports, and `tests/unit/test_package.py` enforces that.

    from ish.interfaces.python.api import Ish

    with Ish("src/") as ish:
        for chunk, score in ish.search("parse the config", limit=5):
            print(score, chunk.path, chunk.symbol)
"""

import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import TracebackType

from ish import bootstrap
from ish.application.filters import Filters, parse_query
from ish.application.progress import ProgressCallback
from ish.application.search import Search
from ish.domain.chunk import Chunk
from ish.domain.match import Match
from ish.interfaces import completion
from ish.settings import Settings, load_settings

log = logging.getLogger(__name__)


class Ish:
    """Search one tree by meaning.

    Hold the index open for as long as the object lives. Close it with
    `close()`, or use the object as a context manager.

    Bring the index up to date once, on the first question, and then
    answer from what is stored. Call `index()` to bring it up to date
    again; a resident interface does that on a thread, so a question
    never waits for a walk of the tree.
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
        self._indexed = False
        self._vocabulary: bootstrap.Vocabulary | None = None

    # ------------------------------------------------------------------
    # Lifetime
    # ------------------------------------------------------------------

    def __enter__(self) -> "Ish":
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
        self._indexed = False

    @property
    def _words(self) -> bootstrap.Vocabulary:
        """Return what the registered languages are called, read once.

        Every keystroke narrows, and reading the registry each time
        would read the user's plugin directory each time.
        """
        if self._vocabulary is None:
            self._vocabulary = bootstrap.build_vocabulary(self.settings)
        return self._vocabulary

    @property
    def _use_case(self) -> Search:
        """Return the use case, building it on first use."""
        if self._search is None:
            self._search = bootstrap.build_search(self.settings, self.path)
        return self._search

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(self, on_progress: ProgressCallback | None = None) -> int:
        """Bring this tree's index up to date. Return the chunks it holds.

        Report progress through *on_progress*, since a first index of a
        large tree runs for minutes.
        """
        held = self._use_case.build_index(self.path, on_progress)
        self._indexed = True
        return held

    def _ensure_indexed(self) -> None:
        """Bring the index up to date once, before the first question."""
        if not self._indexed:
            self.index()

    def refresh_all(self, on_progress: ProgressCallback | None = None) -> list[Path]:
        """Bring every index at or below this tree up to date.

        A search of a parent reads the indexes beneath it and writes to
        none, so refreshing means visiting each tree in turn.
        """
        self.close()
        return bootstrap.refresh_indexes(self.settings, self.path, on_progress)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def filters_of(
        self,
        query: str = "",
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
    ) -> Filters:
        """Return the filters a question will run under.

        A filter typed into the query wins over one passed as an
        argument, which wins over the configured one. Every interface
        resolves them here, so they all rank the three the same way.
        """
        _text, typed = parse_query(query)
        asked = Filters(tuple(lang), under, tuple(type))
        return typed.or_else(asked.or_else(bootstrap.settings_filters(self.settings)))

    def search(
        self,
        query: str,
        limit: int | None = None,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
        hybrid: bool | None = None,
        stored: bool = False,
    ) -> list[Match]:
        """Return the best matching chunks, most similar first.

        Read `lang:`, `under:`, and `type:` out of *query* as well as
        from the arguments, so a line typed by a person works unchanged.
        Raise ``ValueError`` when nothing is left to search for once the
        filter words are taken out, or when a filter is malformed.

        With *stored*, answer from what the index holds without bringing
        it up to date first. A picker asks that way while its refresh
        runs behind the query field.
        """
        text, _typed = parse_query(query)
        if not text:
            raise ValueError("The query holds only filters. Add words to search for.")
        keep = bootstrap.build_result_filter(
            self.settings,
            self.filters_of(query, lang=lang, under=under, type=type),
            self._words,
        )
        if not stored:
            self._ensure_indexed()
        return list(
            self._use_case.search(
                text,
                limit if limit is not None else self.settings.limit,
                keep=keep,
                hybrid=hybrid,
            )
        )

    def chunks(
        self,
        query: str = "",
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
        stored: bool = False,
    ) -> list[Chunk]:
        """Return every indexed chunk the filters allow, unranked.

        Read filter words out of *query* the way `search()` does, so a
        query line with no words left lists what it allows. With
        *stored*, list what the index holds without refreshing it.
        """
        keep = bootstrap.build_result_filter(
            self.settings,
            self.filters_of(query, lang=lang, under=under, type=type),
            self._words,
        )
        if not stored:
            self._ensure_indexed()
        return self._use_case.all_chunks(keep)

    def scan(
        self,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
    ) -> list[Chunk]:
        """Return every chunk the parsers find on disk right now, unranked.

        Read the tree rather than the index, so the answer needs no
        embedding backend and shows the files as they are.
        """
        keep = bootstrap.build_result_filter(
            self.settings,
            self.filters_of(lang=lang, under=under, type=type),
            self._words,
        )
        found = bootstrap.build_scan(self.settings, self.path).run(self.path)
        return [chunk for chunk in found if keep is None or keep(chunk)]

    # ------------------------------------------------------------------
    # Completing
    # ------------------------------------------------------------------

    def complete(self, text: str) -> str:
        """Return *text* with its last filter word finished, or unchanged.

        Grow the word the way a shell does: one answer finishes it and
        adds a space, several grow it as far as they agree. A word that
        fits nothing is left alone, so the key is never destructive.
        """
        return completion.complete(text, self.settings, self.path)

    def candidates(self, text: str) -> list[str]:
        """Return what the last word of *text* could still become.

        Empty when one answer fits or none does. A picker shows these
        beside the query when a completion could not choose.
        """
        return completion.candidates(text, self.settings, self.path)

    def status(self) -> dict[str, object]:
        """Report what is indexed for this tree.

        Count the chunks by language and by kind, so a caller can see
        what a search can reach without listing all of it.

        Read only. A status call that brought the index up to date
        first held the caller for as long as the index run took, and
        reported nothing while it ran. Call ``index()`` for fresh
        numbers.
        """
        chunks = self._use_case.all_chunks()
        sort_into = self._words.categorize
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
            "indexes": sorted(bootstrap.catalog(self.settings).below(self.path)),
            "languages": dict(sorted(languages.items())),
            "types": dict(sorted(kinds.items())),
        }

    def __repr__(self) -> str:
        return f"Ish({str(self.path)!r})"
