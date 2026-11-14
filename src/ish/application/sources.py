"""Read the chunks a search found, for a model that runs elsewhere.

A client that writes an answer with a model of its own needs what the
answer use case reads on one machine: the ranked chunks, the text of
each, and the meaning of the acronyms those texts use. The meanings come
from the glossary of the tree, which the client cannot build, because it
holds no file of the tree.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ish.application.glossary import Definition, Glossary, acronyms
from ish.application.preview import read_lines
from ish.domain.chunk import Chunk
from ish.domain.match import Match


@dataclass(frozen=True, slots=True)
class ChunkText:
    """Hold one ranked chunk and the text it covers.

    The text is empty when the chunk cannot be read, so the rank of
    every other chunk stays as the search gave it.
    """

    chunk: Chunk
    score: float
    text: str


@dataclass(frozen=True, slots=True)
class Sources:
    """Hold the ranked chunks with their text, and the terms they use."""

    chunks: tuple[ChunkText, ...]
    terms: tuple[Definition, ...]


def read_sources(
    matches: Sequence[Match],
    read: Callable[[Chunk], str | None] = read_lines,
    glossary: Glossary | None = None,
) -> Sources:
    """Return *matches* with the text of each, and the acronyms they use.

    Read each chunk through *read*, and add what its text defines to
    *glossary*. Then look up each acronym the texts use, nearest to the
    chunks returned. Leave out an acronym that no text of the tree spells
    out. Sort the terms by name.
    """
    kept = Glossary() if glossary is None else glossary
    chunks: list[ChunkText] = []
    for chunk, score in matches:
        text = read(chunk) or ""
        kept.add(chunk, text)
        chunks.append(ChunkText(chunk, score, text))
    near = [entry.chunk for entry in chunks]
    used = acronyms("\n".join(entry.text for entry in chunks))
    found = (kept.lookup(short, near) for short in used)
    terms = sorted(
        (definition for definition in found if definition is not None),
        key=lambda definition: definition.short.casefold(),
    )
    return Sources(tuple(chunks), tuple(terms))
