"""Read Markdown as sections, one chunk per heading.

Markdown needs nothing beyond the section walk of ``_sections.py``.
"""

import re

from ish.adapters.parser._sections import SectionParser

# A fence is three or more backticks or tildes. A line of hyphens, stars,
# or equal signs is a thematic break or a heading underline, not a fence.
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def markdown_parser() -> SectionParser:
    """Build the Markdown parser."""
    return SectionParser("markdown", frozenset({".md", ".markdown"}), "#", FENCE)
