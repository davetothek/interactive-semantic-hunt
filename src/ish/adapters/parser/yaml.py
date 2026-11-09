"""Read YAML documents, split at the entries they hold.

The splitting lives in ``_structured.py``, which JSON shares.
"""

from ish.adapters.parser._structured import StructuredParser


def yaml_parser() -> StructuredParser:
    """Build the YAML parser."""
    return StructuredParser("yaml", frozenset({".yaml", ".yml"}))
