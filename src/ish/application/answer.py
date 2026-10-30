"""Implement the answer use case — search, then write an answer from what it found.

The language model reads only the chunks the search returned. Each part
of the answer cites the chunks that support it, so a reader can open the
source of every claim. A question the chunks do not cover gets no answer
and the closest chunks instead, never an answer from outside them.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ish.application.ports.language_model import LanguageModel, Source
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)

# Characters of chunk text one question may send. About 15,000 tokens at
# the four characters a token that English text averages.
DEFAULT_BUDGET_CHARS = 60_000


@dataclass(frozen=True, slots=True)
class AnswerPart:
    """Hold one part of an answer and the chunks it cites."""

    text: str
    cites: tuple[Chunk, ...] = ()


@dataclass(frozen=True, slots=True)
class Answer:
    """Hold the result of one question.

    Attributes:
        parts: The answer, in order. Empty when ``covered`` is False.
        covered: False when the chunks the search found do not answer
            the question.
        sources: The chunks the model read, best first. When
            ``covered`` is False, these are the closest sources.
        dropped: Chunks the search found that did not fit the budget.
        model: Name of the model that wrote the answer, or empty when no
            model was asked.
        input_tokens: Tokens the model read.
        output_tokens: Tokens the model wrote.
    """

    parts: tuple[AnswerPart, ...]
    covered: bool
    sources: tuple[Chunk, ...]
    dropped: int = 0
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class AnswerQuestion:
    """Orchestrate one answer: search, fit the budget, ask the model."""

    def __init__(
        self,
        *,
        search: Callable[[str], Sequence[Match]],
        model: LanguageModel,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
    ) -> None:
        """Answer from what *search* returns, written by *model*.

        Take the search as a function, so the answer reads the same
        indexes, filters, and ranking as every other query.
        """
        self._search = search
        self._model = model
        self._budget = budget_chars

    def answer(self, question: str) -> Answer:
        """Answer *question* from the chunks a search returns.

        Raise ``ValueError`` for an empty question. Ask the model nothing
        when the search finds nothing.
        """
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        kept, dropped = self._fit([chunk for chunk, _ in self._search(question)])
        if dropped:
            log.info("%d chunks did not fit the answer budget", dropped)
        if not kept:
            return Answer(parts=(), covered=False, sources=(), dropped=dropped)

        sources = [_source(str(n), chunk) for n, chunk in enumerate(kept, 1)]
        by_id = dict(zip((s.id for s in sources), kept, strict=True))
        reply = self._model.answer(question, sources)
        log.info(
            "%s read %d tokens and wrote %d",
            self._model.model_name,
            reply.input_tokens,
            reply.output_tokens,
        )
        parts = (
            tuple(_part(p.text, p.source_ids, by_id) for p in reply.passages)
            if reply.covered
            else ()
        )
        return Answer(
            parts=parts,
            covered=reply.covered,
            sources=tuple(kept),
            dropped=dropped,
            model=self._model.model_name,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )

    def _fit(self, chunks: list[Chunk]) -> tuple[list[Chunk], int]:
        """Keep the best chunks whose text fits the budget, in rank order.

        Skip a chunk that does not fit and try the next, so one long
        chunk does not push out every shorter one ranked below it.
        """
        kept: list[Chunk] = []
        left = self._budget
        for chunk in chunks:
            if len(chunk.text) <= left:
                kept.append(chunk)
                left -= len(chunk.text)
        return kept, len(chunks) - len(kept)


def _source(source_id: str, chunk: Chunk) -> Source:
    title = f"{chunk.path}:{chunk.start_line}-{chunk.end_line}"
    if chunk.symbol:
        title = f"{title} {chunk.symbol}"
    return Source(id=source_id, title=title, text=chunk.text)


def _part(text: str, ids: Sequence[str], by_id: dict[str, Chunk]) -> AnswerPart:
    """Map cited ids to chunks, and drop an id the model was not given."""
    cites = []
    for source_id in ids:
        if source_id in by_id:
            cites.append(by_id[source_id])
        else:
            log.warning("The model cited source %r, which it was not given", source_id)
    return AnswerPart(text=text, cites=tuple(cites))
