"""Narrow what a search returns, without touching what is indexed.

A query-scope filter must never reach the index. One that decided what
to index would make the next refresh prune everything it excluded, so
``ish --lang markdown`` would silently delete every Python chunk. The
filters here are applied to results only, before the limit, so a
filtered search still returns a full page.
"""

import re
from dataclasses import dataclass

from ish.application.categories import Categorizer, category_of
from ish.application.languages import canonical_language, unique
from ish.application.ranking import ResultFilter
from ish.domain.chunk import Chunk

# Filters written inside the query itself, as `lang:cpp` or `type:doc`.
_INLINE_FILTER = re.compile(r"(?:^|\s)(lang|under|type):(\S+)")


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
            self, "lang", unique(canonical_language(name) for name in self.lang)
        )
        object.__setattr__(
            self, "type", unique(name.strip().lower() for name in self.type)
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
    filters: Filters, categorize: Categorizer | None = None
) -> ResultFilter:
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
