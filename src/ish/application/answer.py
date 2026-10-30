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
from ish.application.preview import read_lines
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)

# Chunks an answer reads in full when a rerank model chose them.
DEFAULT_PICK = 10

# Characters of the start of a chunk that a rerank model reads to judge
# it. Enough for the heading and the first sentences.
PREVIEW_CHARS = 300

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
        dropped: Chunks the search found and the model did not read,
            because they did not fit the budget or had no readable text.
        model: Name of the model that wrote the answer, or empty when no
            model was asked.
        input_tokens: Tokens the model read.
        output_tokens: Tokens the model wrote.
        rerank_input_tokens: Tokens the rerank model read, or 0 when none ran.
        rerank_output_tokens: Tokens the rerank model wrote.
    """

    parts: tuple[AnswerPart, ...]
    covered: bool
    sources: tuple[Chunk, ...]
    dropped: int = 0
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    rerank_input_tokens: int = 0
    rerank_output_tokens: int = 0


class AnswerQuestion:
    """Orchestrate one answer: search, fit the budget, ask the model."""

    def __init__(
        self,
        *,
        search: Callable[[str], Sequence[Match]],
        model: LanguageModel,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
        read: Callable[[Chunk], str | None] = read_lines,
        reranker: LanguageModel | None = None,
        pick: int = DEFAULT_PICK,
    ) -> None:
        """Answer from what *search* returns, written by *model*.

        Take the search as a function, so the answer reads the same
        indexes, filters, and ranking as every other query. Read the text
        of each chunk through *read*, because a stored index holds where
        a chunk is and not what it says.

        With a *reranker*, let it choose at most *pick* of the chunks the
        search returned, from the start of each, before any is read in
        full. A wide search then finds the source a narrow one ranks too
        low, and the answer still reads only a few chunks.
        """
        self._search = search
        self._model = model
        self._budget = budget_chars
        self._read = read
        self._reranker = reranker
        self._pick = pick

    def answer(self, question: str) -> Answer:
        """Answer *question* from the chunks a search returns.

        Raise ``ValueError`` for an empty question. Ask the model nothing
        when the search finds nothing.
        """
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        found = [chunk for chunk, _ in self._search(question)]
        rerank = self._rerank(question, found) if self._reranker else None
        if rerank is not None:
            found, rerank_in, rerank_out = rerank
        else:
            rerank_in = rerank_out = 0
        texts, dropped = self._fit(found)
        if dropped:
            log.info("%d chunks did not fit the answer budget", dropped)
        kept = [chunk for chunk, _ in texts]
        if not kept:
            return Answer(
                parts=(),
                covered=False,
                sources=(),
                dropped=dropped,
                rerank_input_tokens=rerank_in,
                rerank_output_tokens=rerank_out,
            )

        sources = [
            _source(str(n), chunk, text) for n, (chunk, text) in enumerate(texts, 1)
        ]
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
            rerank_input_tokens=rerank_in,
            rerank_output_tokens=rerank_out,
        )

    def _rerank(
        self, question: str, found: list[Chunk]
    ) -> tuple[list[Chunk], int, int] | None:
        """Return the chunks the rerank model chose, best first, and its cost.

        Show the model the start of each readable chunk. Return None when
        the search found nothing, so no model is asked.
        """
        assert self._reranker is not None
        previews: list[Source] = []
        by_id: dict[str, Chunk] = {}
        for n, chunk in enumerate(found, 1):
            text = self._read(chunk)
            if not text or not text.strip():
                continue
            start = " ".join(text.split())[:PREVIEW_CHARS]
            previews.append(_source(str(n), chunk, start))
            by_id[str(n)] = chunk
        if not previews:
            return None
        selection = self._reranker.select(question, previews, self._pick)
        chosen = [by_id[i] for i in selection.ids if i in by_id]
        log.info(
            "%s chose %d of %d chunks, reading %d tokens",
            self._reranker.model_name,
            len(chosen),
            len(previews),
            selection.input_tokens,
        )
        return chosen, selection.input_tokens, selection.output_tokens

    def _fit(self, chunks: list[Chunk]) -> tuple[list[tuple[Chunk, str]], int]:
        """Keep the best chunks whose text fits the budget, in rank order.

        Skip a chunk that does not fit and try the next, so one long
        chunk does not push out every shorter one ranked below it. Skip a
        chunk whose text cannot be read or is empty: a model given a
        title and no text has nothing to answer from.
        """
        kept: list[tuple[Chunk, str]] = []
        left = self._budget
        for chunk in chunks:
            text = self._read(chunk)
            if not text or not text.strip():
                log.warning("Left out %s, which has no readable text", chunk.path)
                continue
            if len(text) <= left:
                kept.append((chunk, text))
                left -= len(text)
        return kept, len(chunks) - len(kept)


def _source(source_id: str, chunk: Chunk, text: str) -> Source:
    title = f"{chunk.path}{chunk.location.render()}"
    if chunk.symbol:
        title = f"{title} {chunk.symbol}"
    return Source(id=source_id, title=title, text=text)


def _part(text: str, ids: Sequence[str], by_id: dict[str, Chunk]) -> AnswerPart:
    """Map cited ids to chunks, and drop an id the model was not given."""
    cites = []
    for source_id in ids:
        if source_id in by_id:
            cites.append(by_id[source_id])
        else:
            log.warning("The model cited source %r, which it was not given", source_id)
    return AnswerPart(text=text, cites=tuple(cites))
