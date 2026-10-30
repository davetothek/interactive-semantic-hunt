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
class Reply:
    """Hold the answer a model wrote, and what it cost.

    Attributes:
        passages: The answer, in order.
        covered: False when the sources do not answer the question.
        input_tokens: Tokens the model read.
        output_tokens: Tokens the model wrote.
    """

    passages: tuple[Passage, ...]
    covered: bool
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
