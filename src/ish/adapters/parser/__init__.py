"""Every language ish reads, and how to add one.

A parser turns one file into chunks. It is a class with three things:

    class TomlParser:
        language = "toml"                   # stamped on every chunk
        suffixes = frozenset({".toml"})     # the files it claims

        def parse(self, path: Path, source: str) -> Sequence[Chunk]:
            ...                             # raise ParseError when nothing parses

That is the whole contract, ``ish.application.ports.parser.Parser``.

To add a language to ish:

1. Write the class in a new module in this package, beside the others.
2. Add one line to ``PARSERS`` below, keyed by the language name.

That line carries everything else ish needs to know about the language:
the other names a reader may type for it, and whether what it reads is
code, prose, or configuration. Nothing outside this file needs editing.
Discovery takes the suffixes from the parser, the ``languages`` option
selects by the key, ``SizeLimited`` caps chunk size for every parser
alike, and ``tests/unit/adapters/parser/test_registry.py`` checks the
new entry against the port.

A parser that belongs to one user rather than to ish goes in
``~/.config/ish/parsers/`` as a module exposing ``parser()``; see
``_plugins.py``. It joins the same table at run time and may replace a
built-in entry of the same language. Such a parser may declare
``aliases`` and a ``category`` of its own, and reads as code otherwise.

Only a language carries a plain name in this package. The machinery
beside them is prefixed with an underscore, so a listing says which is
which.

Keep heavy imports out of module scope. Import a grammar or a library
inside the method that needs it, so listing the languages costs nothing.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ish.adapters.parser.markup import MarkupParser
from ish.adapters.parser.python import PythonParser
from ish.adapters.parser.structured import StructuredParser
from ish.adapters.parser.tree_sitter import cpp_parser
from ish.application.ports.parser import Parser

CODE = "code"
DOC = "doc"
CONFIG = "config"
"""What a language holds. A chunk under a test path is a test whatever it holds."""

CHUNKING_VERSION = "1"
"""How the parsers divide a file into chunks, as a version.

Raise it when a parser, or a cap that wraps one, changes where a file
divides. The next refresh reads every file again under the new
division and embeds only the text it has never seen.
"""


@dataclass(frozen=True, slots=True)
class Language:
    """One registered language: how to build its parser, and what to call it.

    Keep every fact about a language on the line that registers it, so
    adding one never means editing a table somewhere else.
    """

    build: Callable[[], Parser]
    """Return the parser. Called once, when the language is enabled."""

    aliases: frozenset[str] = field(default_factory=frozenset)
    """Other names a reader may type, such as ``yml`` for ``yaml``."""

    category: str = CODE
    """What this language holds: ``code``, ``doc``, or ``config``."""


PARSERS: dict[str, Language] = {
    "python": Language(PythonParser, frozenset({"py", "python3"})),
    # One parser owns C and C++, so every spelling of either names it.
    "cpp": Language(cpp_parser, frozenset({"c", "c++", "cc", "cxx", "h", "hpp"})),
    "markdown": Language(MarkupParser.markdown, frozenset({"md", "mdown"}), DOC),
    "asciidoc": Language(MarkupParser.asciidoc, frozenset({"adoc", "asc"}), DOC),
    "yaml": Language(StructuredParser.yaml, frozenset({"yml"}), CONFIG),
    "json": Language(StructuredParser.json, category=CONFIG),
}
"""Every language ish reads, by the name it is registered under."""


def available_parsers(plugins: bool = True) -> dict[str, Language]:
    """Return every language available: built in, and written by the user.

    A user parser replaces a built-in one of the same language, which is
    how a project teaches ish about its own dialect of a format.
    """
    if not plugins:
        return dict(PARSERS)

    # Import here, so a run that loads no plugin never reads the loader,
    # and so the loader may name Language without an import cycle.
    from ish.adapters.parser._plugins import load_parsers

    return {**PARSERS, **load_parsers()}


def spellings(parsers: Mapping[str, Language]) -> dict[str, str]:
    """Map every name a reader may type to the language it means.

    A language answers to its own name and to each of its aliases.
    """
    names = {name: name for name in parsers}
    for name, language in parsers.items():
        names.update(dict.fromkeys(language.aliases, name))
    return names


def categories(parsers: Mapping[str, Language]) -> dict[str, str]:
    """Map each language to what it holds."""
    return {name: language.category for name, language in parsers.items()}
