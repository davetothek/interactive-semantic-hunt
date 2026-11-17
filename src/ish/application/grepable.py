"""Name the paths that the index leaves out for a model to grep.

Some files hurt a search when they are embedded: generated tables of
one row for each item crowd the top results of a question about the
design. A search never returns a file that the index leaves out, so a
model does not know that it exists. A ``grepable`` entry names such
files and says what they hold. The scan leaves them out, and the
answering model and an agent read the list, so each can grep them when
a question needs them.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

OPTION = "grepable"

SEPARATOR = "="
"""Ends the path of an entry written as one string.

What the files hold can compare values, so the first sign ends the path,
and a path cannot hold one.
"""


@dataclass(frozen=True, slots=True)
class Grepable:
    """Hold one path that the index leaves out, and what its files hold."""

    path: str
    """A regular expression, judged as an ``exclude`` pattern is."""

    holds: str = ""
    """What the files hold, in the words of the operator of the tree."""


def parse_grepable(entries: Sequence[str]) -> tuple[Grepable, ...]:
    """Return one entry for each ``REGEX=WHAT IT HOLDS`` string of *entries*.

    Raise ``ValueError`` that names the option for an entry with no
    path, or for a path that is not a regular expression.
    """
    parsed: list[Grepable] = []
    for entry in entries:
        path, _, holds = entry.partition(SEPARATOR)
        path, holds = path.strip(), holds.strip()
        if not path:
            raise ValueError(
                f"The {OPTION!r} option has an entry with no path: {entry!r}. "
                f"Write it as 'REGEX{SEPARATOR}WHAT THE FILES HOLD'."
            )
        try:
            re.compile(path)
        except re.error as exc:
            raise ValueError(
                f"The {OPTION!r} option has an invalid regular expression "
                f"{path!r}: {exc}"
            ) from exc
        parsed.append(Grepable(path, holds))
    return tuple(parsed)


def listing(entries: Sequence[Grepable]) -> str:
    """Return one line for each entry, for a model to read."""
    return "\n".join(
        f"- `{e.path}` holds {e.holds}" if e.holds else f"- `{e.path}`" for e in entries
    )
