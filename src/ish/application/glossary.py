"""Collect what the acronyms of a tree stand for, from the text of the tree.

An answer uses acronyms that the few chunks it reads never spell out, while
a glossary or another section of the same tree does. Read every text that
passes through an answer, and every chunk a server parses at start, and
keep each definition found in it with the chunk that holds it. A meaning
comes from the documents alone, so nothing is invented.

A definition is accepted in four shapes: ``Auto Exposure (AE)``,
``AE (Auto Exposure)``, a table row ``| AE | Auto Exposure |`` or one
split over two lines, and a description list ``AE:: Auto Exposure``. Each
must pass the test of Schwartz and Hearst (2003): the letters of the
acronym appear in the long form in order, the first at the start of a
word. ``the block (AE)`` is not a definition of AE.
"""

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from ish.domain.chunk import Chunk

# An acronym: a capital letter, then capitals, digits, or a few joiners.
_SHORT = r"[A-Z][A-Z0-9&/-]{1,9}"

_FORWARD = re.compile(rf"\(({_SHORT})\)")
_BACKWARD = re.compile(rf"(?<![\w-])({_SHORT})\s*\(([^()\n]{{3,80}})\)")
_ROW = re.compile(rf"^\|\s*({_SHORT})\s*\|\s*([^|\n]{{3,120}}?)\s*(?:\||$)")
_CELL = re.compile(rf"^\|\s*({_SHORT})\s*$")
_NEXT_CELL = re.compile(r"^\|\s*([^|\n]{3,120}?)\s*$")
_DEFINED = re.compile(rf"^({_SHORT})::\s*(\S.{{2,120}})?$")

# Where a meaning ends and an explanation of it starts.
_STOP = re.compile(r"[.;] | - | – ")

# Markup that wraps words in AsciiDoc and Markdown, and is not the meaning.
_MARKUP = re.compile(r"[*_`+#]")


@dataclass(frozen=True, slots=True)
class Definition:
    """Hold one meaning of an acronym and the chunk that writes it."""

    short: str
    meaning: str
    chunk: Chunk


def long_form(short: str, text: str) -> str | None:
    """Return the end of *text* that *short* abbreviates, or None.

    Walk the letters of *short* from the last, and find each in *text*
    from the end, the first letter at the start of a word. Return the
    words from that start on. The last letter must fall in the last word,
    so every word takes part: ``brightness levels`` does not spell BIN.
    """
    s, ell = len(short) - 1, len(text) - 1
    last_word = text.rstrip().rfind(" ") + 1
    first_match = True
    while s >= 0:
        letter = short[s].lower()
        if not letter.isalnum():
            s -= 1
            continue
        while ell >= 0 and (
            text[ell].lower() != letter
            or (s == 0 and ell > 0 and text[ell - 1].isalnum())
        ):
            ell -= 1
        if ell < 0 or (first_match and ell < last_word):
            return None
        first_match = False
        ell -= 1
        s -= 1
    found = text[ell + 1 :].strip()
    return found if _sound(short, found) else None


def _sound(short: str, meaning: str) -> bool:
    """Return True for words that spell an acronym out, not another code.

    A meaning is longer than its acronym, holds a lowercase letter, and
    neither starts with the acronym nor holds it as a word, so
    ``HDR (HDR-RAW)`` names a mode and does not define HDR.
    """
    folded = meaning.casefold()
    return (
        len(meaning) > len(short)
        and any(c.islower() for c in meaning)
        and "|" not in meaning
        and not folded.startswith(short.casefold())
        and short.casefold() not in folded.split()
    )


def _clean(meaning: str) -> str:
    """Return *meaning* without markup, extra spacing, or what follows a stop.

    A glossary row often adds a sentence after the meaning. Keep the
    meaning alone.
    """
    words = " ".join(_MARKUP.sub("", meaning).split())
    return _STOP.split(words, maxsplit=1)[0].strip(" .,;:")


def definitions(text: str) -> list[tuple[str, str]]:
    """Return each acronym that *text* defines, with its meaning, in order."""
    found: list[tuple[str, str]] = []
    for match in _FORWARD.finditer(text):
        short = match.group(1)
        before = text[: match.start()].rstrip()
        line = before.split("\n")[-1]
        # The long form is at most a few words longer than the acronym.
        words = line.split()[-min(len(short) + 5, len(short) * 2) :]
        meaning = long_form(short, " ".join(words))
        if meaning:
            found.append((short, _clean(meaning)))
    for match in _BACKWARD.finditer(text):
        short, inside = match.groups()
        meaning = _clean(inside)
        if long_form(short, meaning) == meaning:
            found.append((short, meaning))
    lines = text.split("\n")
    for n, line in enumerate(lines):
        row = _ROW.match(line) or _DEFINED.match(line)
        pair = row.groups() if row else None
        if pair is None or pair[1] is None:
            # A cell takes the next cell as its meaning, and a term of a
            # description list the next line.
            following = lines[n + 1] if n + 1 < len(lines) else ""
            cell, term = _CELL.match(line), _DEFINED.match(line)
            after = _NEXT_CELL.match(following) if cell else None
            meaning = after.group(1) if after else following.strip() if term else ""
            head = cell or term
            short = head.group(1) if head else ""
            pair = (short, meaning) if short and meaning else None
        if pair is not None:
            short, meaning = pair[0], _clean(pair[1] or "")
            # The acronym spells the whole meaning, from its first word.
            if meaning and long_form(short, meaning) == meaning:
                found.append((short, meaning))
    return found


class Glossary:
    """Keep the definitions of acronyms found in a tree, safe across threads.

    A server fills it on a thread while answers read it on another, so one
    lock guards every add and every lookup.
    """

    def __init__(self) -> None:
        self._by_short: dict[str, list[Definition]] = {}
        self._seen: set[tuple[Chunk, str]] = set()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_short)

    def add(self, chunk: Chunk, text: str | None) -> None:
        """Keep every definition the *text* of *chunk* writes."""
        if not text:
            return
        found = definitions(text)
        with self._lock:
            for short, meaning in found:
                if (chunk, short) in self._seen:
                    continue
                self._seen.add((chunk, short))
                self._by_short.setdefault(short, []).append(
                    Definition(short, meaning, chunk)
                )

    def lookup(self, short: str, near: Sequence[Chunk] = ()) -> Definition | None:
        """Return the meaning of *short* the tree gives, or None.

        Prefer a definition in a file the answer read, then one in the
        same directory, then the meaning the tree writes most often, so an
        acronym with two meanings takes the one of the documents at hand.
        """
        with self._lock:
            candidates = list(self._by_short.get(short, ()))
        if not candidates:
            return None
        files = {chunk.path for chunk in near}
        directories = {chunk.path.parent for chunk in near}
        for test in (
            lambda d: d.chunk.path in files,
            lambda d: d.chunk.path.parent in directories,
        ):
            close = [d for d in candidates if test(d)]
            if close:
                return close[0]
        counts: dict[str, int] = {}
        for d in candidates:
            counts[d.meaning.casefold()] = counts.get(d.meaning.casefold(), 0) + 1
        best = max(counts, key=lambda meaning: counts[meaning])
        return next(d for d in candidates if d.meaning.casefold() == best)
