"""Language model protocol definition.

Satisfied by adapters that write an answer to a question from a fixed
set of sources, and say which source supports each part of the answer.

The model sees only the sources it is given. An answer that the sources
do not support is not an answer, so a reply says whether the sources
covered the question at all.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Queries one step may ask for that ish runs. A model that asks for more
# has the rest dropped, so the instruction names this number.
SEARCHES_A_TURN = 3


class LanguageModelError(Exception):
    """Report that a language model cannot be built or did not answer."""


@dataclass(frozen=True, slots=True)
class Source:
    """Hold one text the model may cite.

    Attributes:
        id: Key the model cites the source by. Unique within one call.
        title: Short label for the source, such as its path and lines.
        text: The text the model reads.
    """

    id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class Passage:
    """Hold one part of an answer and the sources that support it.

    Attributes:
        text: The words of this part of the answer.
        source_ids: Keys of the sources this part cites. Empty for a part
            that cites nothing, such as a joining sentence.
    """

    text: str
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Row:
    """Hold one row of a table in an answer, and the sources it rests on."""

    cells: tuple[str, ...]
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Block:
    """Hold one part of the layout of an answer.

    Attributes:
        kind: ``summary`` for the short answer, ``prose`` for paragraphs,
            ``steps`` for a procedure, ``table`` for values that compare
            across rows, and ``gaps`` for what the sources leave open.
        heading: The heading of a section, or empty.
        passages: The text, in order. A step is one passage.
        columns: The column names of a table.
        rows: The rows of a table.
    """

    kind: str
    heading: str = ""
    passages: tuple[Passage, ...] = ()
    columns: tuple[str, ...] = ()
    rows: tuple[Row, ...] = ()


@dataclass(frozen=True, slots=True)
class Reply:
    """Hold the answer a model wrote, and what it cost.

    Attributes:
        passages: The answer, in order.
        covered: False when the sources do not answer the question.
        input_tokens: Tokens the model read.
        output_tokens: Tokens the model wrote.
        searches: Queries the model asks to run before it answers. A reply
            that holds any is not an answer yet.
        blocks: The layout of the answer, when the model gave one. Every
            passage of the blocks is also in *passages*, in order, so a
            reader of the flat text misses no claim and no citation.
    """

    passages: tuple[Passage, ...]
    covered: bool
    input_tokens: int = 0
    output_tokens: int = 0
    searches: tuple[str, ...] = ()
    blocks: tuple[Block, ...] = ()


@dataclass(frozen=True, slots=True)
class Selection:
    """Hold the sources a model chose to read, and what choosing cost.

    Attributes:
        ids: Keys of the chosen sources, best first.
        input_tokens: Tokens the model read.
        output_tokens: Tokens the model wrote.
    """

    ids: tuple[str, ...]
    input_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class LanguageModel(Protocol):
    """Contract for answering a question from given sources."""

    model_name: str
    """Name of the model that writes the answer."""

    def answer(self, question: str, sources: Sequence[Source]) -> Reply:
        """Answer *question* from *sources* only.

        Raise ``LanguageModelError`` when the model does not answer. Never
        answer from knowledge outside *sources*.
        """
        ...

    def step(
        self,
        question: str,
        sources: Sequence[Source],
        searched: Sequence[str],
        turns_left: int,
    ) -> Reply:
        """Answer from *sources*, or ask for more searches first.

        Return a reply with ``searches`` to have them run, and their
        results added to *sources* on the next call. *searched* holds the
        queries already run. With no turns left, answer, or say the
        sources do not cover the question. Never answer from knowledge
        outside *sources*.
        """
        ...

    def select(
        self, question: str, candidates: Sequence[Source], limit: int
    ) -> Selection:
        """Choose at most *limit* of *candidates* worth reading in full.

        Each candidate holds the start of a source, not all of it, so a
        wide search costs few tokens to sort. Raise ``LanguageModelError``
        when the model does not reply.
        """
        ...
