"""Read JSON documents, split at the entries they hold.

YAML is a superset of JSON, so the YAML reader of ``_structured.py``
serves both.
"""

from ish.adapters.parser._structured import StructuredParser


def json_parser() -> StructuredParser:
    """Build the JSON parser."""
    return StructuredParser("json", frozenset({".json"}))
