"""Name languages the way a reader types them.

A parser owns several file kinds, so the name it is registered under is
not always the one that comes to mind: the C++ parser reads C, and few
people write "asciidoc" when they mean adoc. Resolve every spelling to
the one name ish stores a language under, so the filter, the display,
and every comparison agree.
"""

from collections.abc import Iterable

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


def language_names() -> tuple[str, ...]:
    """Return every name a user may type for a language, sorted."""
    return tuple(sorted(LANGUAGE_ALIASES))


def unique(names: Iterable[str]) -> tuple[str, ...]:
    """Return the names once each, in the order they were given.

    Two spellings of one language resolve to a single name, so keep the
    display and the filter free of the repeat.
    """
    seen: dict[str, None] = {}
    for name in names:
        seen[name] = None
    return tuple(seen)
