"""Read Markdown as sections, one chunk per heading.

Markdown needs nothing beyond the section walk of ``_sections.py``.
"""

from ish.adapters.parser._sections import SectionParser


def markdown_parser() -> SectionParser:
    """Build the Markdown parser."""
    return SectionParser("markdown", frozenset({".md", ".markdown"}), "#")
