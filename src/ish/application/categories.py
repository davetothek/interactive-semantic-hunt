"""Sort a chunk into what it is for: code, doc, test, or config.

The categories partition the corpus, so every chunk has exactly one. The
path is consulted before the language, so a YAML fixture under tests/ is
a test rather than config. A repository may add rules of its own, as
``type:regex``, because a naming convention belongs to a repository and
not to a language.
"""

import re
from collections.abc import Callable, Sequence

from ish.domain.chunk import Chunk

Categorizer = Callable[[Chunk], str]
"""Say which category a chunk falls into."""

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


def compile_categories(
    patterns: Sequence[str],
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
