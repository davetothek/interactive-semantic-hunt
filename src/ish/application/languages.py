"""Resolve the name a reader types to the name a parser is registered under.

A parser owns several file kinds, so the name it is registered under is
not always the one that comes to mind: the C++ parser reads C, and few
people write "asciidoc" when they mean adoc. Each language declares the
names it answers to where it is registered, so this module holds no
table of its own and a new language brings its own vocabulary.
"""

from collections.abc import Callable, Iterable, Mapping

LanguageResolver = Callable[[str], str]
"""Return the name ish stores a language under, for any spelling of it."""


def resolve_with(names: Mapping[str, str]) -> LanguageResolver:
    """Return a resolver over *names*, a spelling to language map.

    Leave an unknown name alone, so a filter for a language no parser
    reads returns nothing rather than an error.
    """

    def resolve(name: str) -> str:
        spelling = name.strip().lower()
        return names.get(spelling, spelling)

    return resolve


def as_typed(name: str) -> str:
    """Return the name as typed, tidied. Use where no registry is to hand."""
    return name.strip().lower()


def unique(names: Iterable[str]) -> tuple[str, ...]:
    """Return the names once each, in the order they were given.

    Two spellings of one language resolve to a single name, so keep the
    display and the filter free of the repeat.
    """
    seen: dict[str, None] = {}
    for name in names:
        seen[name] = None
    return tuple(seen)
