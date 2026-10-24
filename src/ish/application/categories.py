"""Sort a chunk into what it is for: code, doc, test, or config.

The categories partition the corpus, so every chunk has exactly one. The
path is consulted before the language, so a YAML fixture under tests/ is
a test rather than config. A repository may add rules of its own, as
``type:regex``, because a naming convention belongs to a repository and
not to a language.

What a language itself holds is declared where the language is
registered, and reaches this module as a mapping. So adding a language
that holds prose or configuration needs no edit here.
"""

import re
from collections.abc import Callable, Mapping, Sequence

from ish.domain.chunk import Chunk

Categorizer = Callable[[Chunk], str]
"""Say which category a chunk falls into."""

CODE = "code"
DOC = "doc"
TEST = "test"
CONFIG = "config"

# The directories where tests live. A path with one of these as a whole
# segment is a test path.
_TEST_DIRS = frozenset(
    {"test", "tests", "spec", "specs", "__tests__", "testdata", "fixtures"}
)

# The categories a chunk can fall into. Every chunk has exactly one.
TYPES = (CODE, DOC, TEST, CONFIG)


def _is_test_path(path: str) -> bool:
    """Return True when the POSIX *path* is in a test directory or names a test.

    Judge a chunk by its path rather than its language, so a fixture
    counts as a test alongside the code that reads it. A file name is a
    test when it is ``conftest.py``, or when an extension follows a stem
    that starts with ``test_`` or ends with ``_test`` or ``.test``. Test
    the segments with string operations. A filtered search asks about
    every chunk, and a regular expression over the whole path costs
    thirteen times as much.
    """
    parts = path.split("/")
    if not _TEST_DIRS.isdisjoint(parts):
        return True
    name = parts[-1]
    if name == "conftest.py":
        return True
    # The stem and the extension each hold at least one character, so a
    # marker that touches either end of the name does not count.
    body = name[1:-1]
    if "_test." in body or ".test." in body:
        return True
    return name.startswith("test_") and "." in name[6:-1]


def by_language(languages: Mapping[str, str]) -> Categorizer:
    """Return the built-in reading: a test path, then what the language holds.

    Rank the path above the language, because a YAML fixture belongs
    with the tests it feeds rather than with the configuration. A
    language *languages* does not name holds code.
    """

    def category_of(chunk: Chunk) -> str:
        if _is_test_path(chunk.path.as_posix()):
            return TEST
        return languages.get(chunk.language, CODE)

    return category_of


category_of = by_language({})
"""Sort a chunk with no registry to hand: a test by its path, else code."""


def compile_categories(
    patterns: Sequence[str],
    languages: Mapping[str, str] | None = None,
) -> Categorizer:
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

    fallback = category_of if languages is None else by_language(languages)
    if not compiled:
        return fallback

    def categorize(chunk: Chunk) -> str:
        path = chunk.path.as_posix()
        for name, pattern in compiled:
            if pattern.search(path):
                return name
        return fallback(chunk)

    return categorize
