"""Find the tables of an AsciiDoc section in its expanded text.

A table may open inside an include, so it is found in the text the
includes bring in, and each record names the lines of the file that
hold its delimiters.
"""

import re
from dataclasses import dataclass

# Characters an include may bring into a section before it gets a chunk of
# its own. A sentence or a short list stays inline. A register table does
# not, because it would outweigh the prose around it.
INLINE_CHARS = 600

# A table opens and closes on a line of three or more equal signs after a
# pipe.
_DELIMITER = re.compile(r"^\|={3,}\s*$")
_CLOSE = "|===\n"


@dataclass(frozen=True, slots=True)
class _Table:
    """A table in a section: the file lines it spans, its size, and its head.

    A table may open inside an include, so it is found in the expanded
    text, and *opens* and *closes* are the lines of the file whose text
    holds its delimiters.
    """

    opens: int
    closes: int
    size: int
    prefix: str


def _tables(
    expanded: list[str], start: int, end: int, limit: int
) -> tuple[dict[int, _Table], dict[int, _Table]]:
    """Find the tables of lines *start* to *end*, in their expanded text.

    Return the tables by the line that opens each, and, for every line at
    whose end a table is still open, that table. The head of a table is
    its attribute line, the delimiter, and its header row: the rows
    before the first blank line, when the attribute line names a header
    or one row stands before that blank. A head that would fill a
    quarter of a piece is not repeated.
    """
    opened: dict[int, _Table] = {}
    open_at: dict[int, int] = {}
    current: dict | None = None
    previous = ""
    for number in range(start, end + 1):
        for line in expanded[number - 1].splitlines(keepends=True):
            if _DELIMITER.match(line):
                if current is None:
                    current = {
                        "opens": number,
                        "attributes": previous if previous.startswith("[") else "",
                        "named": previous.startswith("[") and "header" in previous,
                        "header": [],
                        "heading": True,
                        "size": len(line),
                    }
                else:
                    current["size"] += len(line)
                    opened[current["opens"]] = _close(current, number, limit)
                    current = None
                previous = line
                continue
            if current is not None:
                current["size"] += len(line)
                if current["heading"]:
                    if line.strip():
                        current["header"].append(line)
                    else:
                        current["heading"] = False
            if line.strip():
                previous = line
        if current is not None:
            open_at[number] = current["opens"]
    if current is not None:
        opened[current["opens"]] = _close(current, end, limit)
    return opened, {number: opened[opens] for number, opens in open_at.items()}


def _close(current: dict, closes: int, limit: int) -> _Table:
    """Freeze what is known of a table into its record."""
    header = current["header"]
    keep = (current["named"] or len(header) == 1) and not current["heading"]
    prefix = current["attributes"] + _CLOSE + ("".join(header) + "\n" if keep else "")
    if len(prefix) > limit // 4:
        prefix = _CLOSE
    return _Table(current["opens"], closes, current["size"], prefix)
