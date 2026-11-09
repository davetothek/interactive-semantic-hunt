"""AsciiDoc: sections, includes, tables, and figures.

``parser.py`` holds the parser. ``_includes.py`` resolves includes, and
``_tables.py`` finds tables in the text they bring in.
"""

from ish.adapters.parser.asciidoc.parser import AsciiDocParser

__all__ = ["AsciiDocParser"]
