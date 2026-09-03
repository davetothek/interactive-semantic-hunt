"""Implement the search use case — refresh the index, then query it."""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ish.application.index import Index
from ish.application.ports.embedder import Embedder
from ish.application.ports.vector_store import VectorStore
from ish.application.scan import Scan
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)


# Filters written inside the query itself, as `lang:cpp` or `type:doc`.
_INLINE_FILTER = re.compile(r"(?:^|\s)(lang|under|type):(\S+)")

# What a language is for. A language named in neither table is code.
_DOC_LANGUAGES = frozenset({"asciidoc", "markdown"})
_CONFIG_LANGUAGES = frozenset({"json", "toml", "yaml"})

# Where a test lives. Judge a chunk by its path rather than its language,
# so a fixture counts as a test alongside the code that reads it.
_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|specs?|__tests__|testdata|fixtures)(?:/|$)"
    r"|(?:^|/)(?:test_[^/]+|[^/]+_test|[^/]+\.test)\.[^/]+$"
    r"|(?:^|/)conftest\.py$"
)

# The categories a chunk can fall into. Every chunk has exactly one.
TYPES = ("code", "doc", "test", "config")

# The names a reader is likely to type for a language ish stores under
# another name. A parser owns several file kinds, so the name it is
# registered under is not always the one that comes to mind: the C++
# parser reads C, and few people write "asciidoc" when they mean adoc.
LANGUAGE_ALIASES = {
    "c": "cpp",
    "c++": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "h": "cpp",
    "hpp": "cpp",
    "adoc": "asciidoc",
    "asc": "asciidoc",
    "md": "markdown",
    "mdown": "markdown",
    "py": "python",
    "python3": "python",
    "yml": "yaml",
}


def canonical_language(name: str) -> str:
    """Return the name ish stores a language under.

    Leave an unknown name alone, so a filter for a language no parser
    reads returns nothing rather than an error.
    """
    key = name.strip().lower()
    return LANGUAGE_ALIASES.get(key, key)


def _unique(names) -> tuple[str, ...]:
    """Return the names once each, in the order they were given.

    Two spellings of one language resolve to a single name, so keep the
    display and the filter free of the repeat.
    """
    seen: dict[str, None] = {}
    for name in names:
        seen[name] = None
    return tuple(seen)


def language_names() -> tuple[str, ...]:
    """Return every name a user may type for a language, sorted."""
    return tuple(sorted(LANGUAGE_ALIASES))


def compile_categories(
    patterns: Sequence[str],
) -> Callable[[Chunk], str]:
    """Turn ``type:regex`` rules into a function that sorts a chunk.

    A naming convention is a property of a repository, not of a
    language, so let it be written down rather than guessed. Measured on
    one firmware tree that numbers its directories: no general rule
    matched them, which filed 7,395 test chunks as code.

    Try each rule in order against the path and take the first that
    matches. Fall back to the built-in reading, so a rule adds to the
    default rather than replacing it.
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for rule in patterns:
        name, _, expression = rule.partition(":")
        name = name.strip().lower()
        if not expression:
            raise ValueError(
                f"The type pattern {rule!r} needs the form 'type:regex', "
                f"for example 'test:/[0-9.]*Test'."
            )
        if name not in TYPES:
            raise ValueError(
                f"The type pattern {rule!r} names an unknown type {name!r}. "
                f"Valid types: {', '.join(TYPES)}."
            )
        try:
            compiled.append((name, re.compile(expression)))
        except re.error as exc:
            raise ValueError(
                f"The type pattern {rule!r} has an invalid regular expression: {exc}"
            ) from exc

    if not compiled:
        return category_of

    def categorize(chunk: Chunk) -> str:
        path = chunk.path.as_posix()
        for name, pattern in compiled:
            if pattern.search(path):
                return name
        return category_of(chunk)

    return categorize


def category_of(chunk: Chunk) -> str:
    """Return what a chunk is for: test, doc, config, or code.

    Rank the path above the language, because a YAML fixture belongs
    with the tests it feeds rather than with the configuration.
    """
    if _TEST_PATH.search(chunk.path.as_posix()):
        return "test"
    if chunk.language in _DOC_LANGUAGES:
        return "doc"
    if chunk.language in _CONFIG_LANGUAGES:
        return "config"
    return "code"


@dataclass(frozen=True, slots=True)
class Filters:
    """Say which results to show, without saying what to index.

    Group the narrowing options together so that adding one does not
    widen the signature of every interface that carries them.
    """

    lang: tuple[str, ...] = ()
    under: str = ""
    type: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Store the canonical name of every language and kind.

        Normalize once, at the edge, so that everything downstream —
        the filter, the display, and any comparison — agrees on one
        spelling of each name.
        """
        object.__setattr__(
            self, "lang", _unique(canonical_language(name) for name in self.lang)
        )
        object.__setattr__(
            self, "type", _unique(name.strip().lower() for name in self.type)
        )

    def __bool__(self) -> bool:
        """Report whether anything is narrowed."""
        return bool(self.lang or self.under or self.type)

    def or_else(self, other: "Filters") -> "Filters":
        """Fill each empty field from *other*.

        A filter typed into the query wins over the configured one, so
        one search can be narrowed and widened again without restarting.
        """
        return Filters(
            lang=self.lang or other.lang,
            under=self.under or other.under,
            type=self.type or other.type,
        )

    def describe(self) -> str:
        """Render the active filters for display, or an empty string."""
        parts = []
        if self.lang:
            parts.append(f"lang: {', '.join(self.lang)}")
        if self.type:
            parts.append(f"type: {', '.join(self.type)}")
        if self.under:
            parts.append(f"under: {self.under}")
        return "   ".join(parts)


def parse_query(text: str) -> tuple[str, Filters]:
    """Split a typed query into its text and the filters written into it.

    Return the query with the filter words removed and the filters they
    named. Removing them matters: the embedder should see what the user
    is looking for, not how they narrowed it.
    """
    languages: list[str] = []
    types: list[str] = []
    under = ""

    for key, value in _INLINE_FILTER.findall(text):
        if key == "lang":
            languages.extend(part for part in value.split(",") if part)
        elif key == "type":
            types.extend(part for part in value.split(",") if part)
        else:
            under = value

    # Collapse the gaps the removed words leave, so the embedder sees
    # the sentence the user meant rather than its spacing.
    remaining = " ".join(_INLINE_FILTER.sub(" ", text).split())
    return remaining, Filters(tuple(languages), under, tuple(types))


def build_result_filter(
    filters: Filters, categorize: Callable[[Chunk], str] | None = None
) -> Callable[[Chunk], bool] | None:
    """Build the result filter, or None when nothing narrows the view.

    These narrow what a search returns. They must never reach the index,
    because a filter that decided what to index would make the next run
    prune everything it excluded.
    """
    languages = frozenset(filters.lang)
    types = frozenset(filters.type)
    sort_into = categorize or category_of
    try:
        pattern = re.compile(filters.under) if filters.under else None
    except re.error as exc:
        raise ValueError(
            f"The 'under' option has an invalid regular expression "
            f"{filters.under!r}: {exc}"
        ) from exc

    if not languages and not types and pattern is None:
        return None

    def keep(chunk: Chunk) -> bool:
        if languages and chunk.language not in languages:
            return False
        if types and sort_into(chunk) not in types:
            return False
        return pattern is None or bool(pattern.search(chunk.path.as_posix()))

    return keep


class Search:
    """Orchestrate semantic search over a directory tree."""

    def __init__(
        self,
        *,
        scan: Scan,
        embedder: Embedder,
        vector_store: VectorStore,
        reindex: bool = False,
        hybrid: bool = True,
        keep: Callable[[Chunk], bool] | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._reindex = reindex
        self._hybrid = hybrid
        self._keep = keep
        self._index = Index(
            scan=scan,
            embedder=embedder,
            vector_store=vector_store,
        )

    def close(self) -> None:
        """Release the store."""
        self._vector_store.close()

    def build_index(
        self, root: Path, on_progress: Callable[[str], None] | None = None
    ) -> Sequence[Chunk] | None:
        """Bring the index in step with *root*.

        Return the chunks the store now holds, or None when it holds none.
        """
        if not getattr(self._vector_store, "writable", True):
            # Reading several indexes at once. Refreshing would have to
            # choose one to write to, and any choice would be wrong.
            log.info("Searching stored indexes without refreshing")
            chunks = self.all_chunks()
            return chunks or None

        if self._reindex:
            log.info("Discarding the stored index for %s", root)
            self._vector_store.clear()
            self._reindex = False

        stats = self._index.refresh(root, on_progress)
        log.info(
            "Index ready: %d files, %d chunks written, %d vectors embedded",
            stats.files_seen,
            stats.chunks_indexed,
            stats.vectors_embedded,
        )
        chunks = self.all_chunks()
        if not chunks:
            log.warning("No chunks found to index.")
            return None
        return chunks

    def all_chunks(self, keep: Callable[[Chunk], bool] | None = None) -> list[Chunk]:
        """Return the chunks the store holds, for a plain listing.

        Apply the same result filter a search would, so the listing and
        the search agree on what is in view.
        """
        chosen = keep or self._keep
        chunks = self._vector_store.chunks()
        if chosen is None:
            return list(chunks)
        return [chunk for chunk in chunks if chosen(chunk)]

    def search(
        self,
        query: str,
        limit: int = 5,
        keep: Callable[[Chunk], bool] | None = None,
        hybrid: bool | None = None,
    ) -> Sequence[tuple[Chunk, float]]:
        """Query the vector store with the semantic query.

        Accept a filter for this call alone, so a long-lived interface
        can narrow one search without rebuilding anything.
        """
        log.info("Embedding search query...")
        query_vector = self._embedder.embed_query(query)
        if not query_vector:
            return []

        log.info("Searching vector store...")
        use_hybrid = self._hybrid if hybrid is None else hybrid
        return self._vector_store.search(
            query_vector,
            query if use_hybrid else "",
            limit=limit,
            keep=keep or self._keep,
        )

    def run(
        self,
        root: Path,
        query: str,
        limit: int = 5,
        keep: Callable[[Chunk], bool] | None = None,
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the best matching chunks for a semantic query."""
        if self.build_index(root) is None:
            return []
        return self.search(query, limit, keep=keep)
