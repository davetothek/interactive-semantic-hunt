"""Every parser ish ships, and how to add one.

A parser turns one file into chunks. It is a class with three things:

    class TomlParser:
        language = "toml"                   # stamped on every chunk
        suffixes = frozenset({".toml"})      # the files it claims

        def parse(self, path: Path, source: str) -> Sequence[Chunk]:
            ...                              # raise ParseError when nothing parses

That is the whole contract, ``ish.application.ports.parser.Parser``.

To add a language to ish:

1. Write the class in a new module in this package, beside the others.
2. Add one line to ``PARSERS`` below, keyed by the language name.

Nothing else is required. Discovery takes the suffixes from the parser,
the ``languages`` option selects by the key, ``SizeLimited`` caps chunk
size for every parser alike, and the tests in
``tests/unit/test_bootstrap.py`` check the new entry against the port.

Two optional touches live in the application layer, because they are
vocabulary for the query line rather than parsing:

- ``ish.application.languages.LANGUAGE_ALIASES`` — other names a reader
  may type for the language, such as ``yml`` for ``yaml``.
- ``ish.application.categories`` — whether the language is ``doc`` or
  ``config``. Anything not listed there is ``code``.

A parser that belongs to one user rather than to ish goes in
``~/.config/ish/parsers/`` as a module exposing ``parser()``; see
``plugins.py``. It joins the same table at run time and may replace a
built-in entry of the same language.

Keep heavy imports out of module scope. Import a grammar or a library
inside the method that needs it, so listing the parsers costs nothing.
"""

from collections.abc import Callable

from ish.adapters.parser.markup import MarkupParser
from ish.adapters.parser.plugins import load_parsers
from ish.adapters.parser.python import PythonParser
from ish.adapters.parser.structured import StructuredParser
from ish.adapters.parser.tree_sitter import cpp_parser
from ish.application.ports.parser import Parser

PARSERS: dict[str, Callable[[], Parser]] = {
    "python": PythonParser,
    "markdown": MarkupParser.markdown,
    "asciidoc": MarkupParser.asciidoc,
    "cpp": cpp_parser,
    "yaml": StructuredParser.yaml,
    "json": StructuredParser.json,
}
"""Source parsers by language name. Register a new parser here."""


def available_parsers(plugins: bool = True) -> dict[str, Callable[[], Parser]]:
    """Return every parser available: built in, and written by the user.

    A user parser replaces a built-in one of the same language, which is
    how a project teaches ish about its own dialect of a format.
    """
    if not plugins:
        return dict(PARSERS)
    return {**PARSERS, **load_parsers()}
