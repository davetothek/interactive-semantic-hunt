"""Complete a filter word typed into a query.

The filters are worth little if their values have to be remembered, and
the names a repository uses are not guessable: `lang:` takes a parser
name or one of its aliases, `type:` takes one of four words, and
`under:` takes an expression matched against a path.

Complete the way a shell does. One candidate finishes the word and adds
a space; several finish as far as they agree, so pressing again after
typing one more character keeps making progress.
"""

from collections.abc import Sequence
from pathlib import Path

from ish import bootstrap
from ish.application.search import TYPES, language_names
from ish.settings import Settings

# The filters a query may carry. `parse_query()` reads these same words.
KEYS = ("lang:", "type:", "under:")

# How many directories deep to offer for `under:`. A path filter is
# usually a subtree of the project, not a leaf.
UNDER_DEPTH = 2


def complete(text: str, settings: Settings, root: Path) -> str:
    """Return *text* with its last word completed, or unchanged.

    Leave the text alone when nothing matches, so pressing the key is
    never destructive.
    """
    head, _, word = text.rpartition(" ")
    prefix = f"{head} " if head else ""

    key, colon, value = word.partition(":")
    if colon and f"{key}:" in KEYS:
        candidates = _values_for(f"{key}:", settings, root)
        grown, exact = _grow(value, candidates)
        return f"{prefix}{key}:{grown}{' ' if exact else ''}"

    grown, exact = _grow(word, KEYS)
    # A key ends in a colon, which is where the value goes, so no space.
    return f"{prefix}{grown}" if grown else text


def candidates(text: str, settings: Settings, root: Path) -> list[str]:
    """Return what the last word could still become.

    A completion that cannot choose should still say what it was
    choosing between, or pressing the key looks like nothing happened.
    """
    _head, _, word = text.rpartition(" ")
    key, colon, value = word.partition(":")
    if colon and f"{key}:" in KEYS:
        offered = _values_for(f"{key}:", settings, root)
        typed = value
    else:
        offered, typed = list(KEYS), word
    matching = [c for c in offered if c.startswith(typed)]
    return [] if len(matching) < 2 else matching


def _values_for(key: str, settings: Settings, root: Path) -> Sequence[str]:
    """Return what may follow *key*."""
    if key == "type:":
        return sorted(TYPES)
    if key == "lang:":
        return sorted(set(bootstrap.all_parsers(settings)) | set(language_names()))
    return _directories(root)


def _directories(root: Path) -> list[str]:
    """Return the subtrees of *root*, written as a path filter would be.

    `under` is matched against the whole path, so offer the form the
    documentation uses and a reader would write by hand.
    """
    found: list[str] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > UNDER_DEPTH:
            return
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            found.append(f"/{entry.relative_to(root).as_posix()}/")
            walk(entry, depth + 1)

    walk(root, 1)
    return found


def _grow(typed: str, candidates: Sequence[str]) -> tuple[str, bool]:
    """Return how far *typed* can grow, and whether it is now complete.

    Grow to the longest opening every match shares, which is what a
    shell does: it never chooses for the reader.
    """
    matching = [c for c in candidates if c.startswith(typed)]
    if not matching:
        return typed, False
    if len(matching) == 1:
        return matching[0], True

    shared = matching[0]
    for candidate in matching[1:]:
        while not candidate.startswith(shared):
            shared = shared[:-1]
    return shared, False
