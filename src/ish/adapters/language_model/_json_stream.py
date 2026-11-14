"""Find each object and array of a JSON text as it arrives, once it is whole.

A model that streams its reply sends the JSON a few characters at a
time. A reader wants each part of the answer as soon as it is written,
and a part is a value it can read only once its last character is in.
So the scanner keeps the text, and reports each object and array with
its path, such as ``("sections", 2)``, at the character that closes it.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field

Path = tuple[str | int, ...]


@dataclass(slots=True)
class _Open:
    """One object or array that has opened and not yet closed."""

    kind: str
    start: int
    path: Path
    # The key an object is at, and whether a key comes next.
    key: str = ""
    expects_key: bool = True
    # The index an array is at.
    index: int = 0


@dataclass(slots=True)
class JsonValues:
    """Read a JSON text in pieces, and yield each object and array it completes."""

    _text: str = ""
    _open: list[_Open] = field(default_factory=list)
    _in_string: bool = False
    _escaped: bool = False
    _string_start: int = 0

    def feed(self, piece: str) -> Iterator[tuple[Path, object]]:
        """Add *piece*, and yield the path and value of each value it closes."""
        base = len(self._text)
        self._text += piece
        for offset, char in enumerate(piece):
            position = base + offset
            if self._in_string:
                self._in_quotes(char, position)
                continue
            if char == '"':
                self._in_string, self._string_start = True, position
            elif char in "{[":
                self._open.append(_Open(char, position, self._child_path()))
            elif char in "}]" and self._open:
                done = self._open.pop()
                yield done.path, json.loads(self._text[done.start : position + 1])
            elif char == ":" and self._open:
                self._open[-1].expects_key = False
            elif char == "," and self._open:
                top = self._open[-1]
                top.index += 1
                top.expects_key = True

    def _in_quotes(self, char: str, position: int) -> None:
        """Follow a string to its end, and take it as the key when one is due."""
        if self._escaped:
            self._escaped = False
        elif char == "\\":
            self._escaped = True
        elif char == '"':
            self._in_string = False
            top = self._open[-1] if self._open else None
            if top is not None and top.kind == "{" and top.expects_key:
                top.key = json.loads(self._text[self._string_start : position + 1])

    def _child_path(self) -> Path:
        """Return the path of a value that starts here."""
        if not self._open:
            return ()
        top = self._open[-1]
        step: str | int = top.key if top.kind == "{" else top.index
        return (*top.path, step)
