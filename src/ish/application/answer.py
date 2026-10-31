"""Implement the answer use case — search, then write an answer from what it found.

The language model reads only the chunks the search returned. Each part
of the answer cites the chunks that support it, so a reader can open the
source of every claim. A question the chunks do not cover gets no answer
and the closest chunks instead, never an answer from outside them.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ish.application.ports.language_model import (
    SEARCHES_A_TURN,
    Block,
    LanguageModel,
    Reply,
    Source,
)
from ish.application.preview import read_lines
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)

# Chunks an answer reads in full when a rerank model chose them.
DEFAULT_PICK = 10

# Characters of the start of a chunk that a rerank model reads to judge
# it. Enough for the heading and the first sentences.
PREVIEW_CHARS = 300

# Chunks each follow-up search adds.
CHUNKS_A_SEARCH = 5

# Characters of chunk text one question may send. About 15,000 tokens at
# the four characters a token that English text averages.
DEFAULT_BUDGET_CHARS = 60_000


@dataclass(frozen=True, slots=True)
class AnswerPart:
    """Hold one part of an answer and the chunks it cites."""

    text: str
    cites: tuple[Chunk, ...] = ()


SEARCHING = "searching"
"""Running the search the question starts with."""
CHOOSING = "choosing"
"""The rerank model choosing which chunks to read."""
READING = "reading"
"""Reading the chosen chunks from their files."""
WRITING = "writing"
"""The language model writing the answer, or asking for searches."""
FOLLOWING = "following"
"""Running a search the language model asked for."""


@dataclass(frozen=True, slots=True)
class AnswerStep:
    """Report one step of an answer, as a value an interface renders.

    A model call takes seconds, and a command that shows nothing then
    cannot be told from one that has stopped. *count* is the chunks the
    step works on, *model* the model that runs it, *query* a follow-up
    search, and *turn* of *turns* the call to the answering model.
    """

    stage: str
    count: int = 0
    model: str = ""
    query: str = ""
    turn: int = 0
    turns: int = 0

    def __str__(self) -> str:
        """Render the step as one line a person can read."""
        if self.stage == SEARCHING:
            return "Searching"
        if self.stage == CHOOSING:
            return f"{self.model} choosing what to read from {self.count} chunks"
        if self.stage == READING:
            return f"Reading {self.count} chunks"
        if self.stage == FOLLOWING:
            return f"Searching again: {self.query}"
        turn = f", turn {self.turn} of {self.turns}" if self.turns > 1 else ""
        return f"{self.model} writing the answer{turn}"


AnswerCallback = Callable[[AnswerStep], None]


def _quiet(step: AnswerStep) -> None:
    """Report nothing, for a caller that shows no progress."""


@dataclass(frozen=True, slots=True)
class AnswerRow:
    """Hold one row of a table in an answer, and the chunks it cites."""

    cells: tuple[str, ...]
    cites: tuple[Chunk, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerBlock:
    """Hold one part of the layout of an answer, with its citations as chunks.

    *kind* is ``summary``, ``prose``, ``steps``, ``table``, or ``gaps``.
    """

    kind: str
    heading: str = ""
    parts: tuple[AnswerPart, ...] = ()
    columns: tuple[str, ...] = ()
    rows: tuple[AnswerRow, ...] = ()


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
        searches: Follow-up queries the model asked for and ish ran, in
            order. Empty when the model answered from the first search.
        turns: Calls to the model that wrote the answer.
        blocks: The layout of the answer, when the model gave one: a short
            answer, sections of prose, steps, or tables, and what the
            sources leave open. *parts* holds the same claims flat.
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
    searches: tuple[str, ...] = ()
    turns: int = 0
    blocks: tuple[AnswerBlock, ...] = ()


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
        turns: int = 0,
        on_step: AnswerCallback = _quiet,
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

        With *turns* above 0, let the model ask for follow-up searches up
        to that many times before it answers. Each search runs through
        *search*, so it reads the same indexes and filters, and adds the
        best chunks the model has not read, within the same budget.
        """
        self._search = search
        self._model = model
        self._budget = budget_chars
        self._read = read
        self._reranker = reranker
        self._pick = pick
        self._turns = turns
        self._on_step = on_step

    def answer(self, question: str) -> Answer:
        """Answer *question* from the chunks a search returns.

        Raise ``ValueError`` for an empty question. Ask the model nothing
        when the search finds nothing.
        """
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        self._on_step(AnswerStep(SEARCHING))
        found = [chunk for chunk, _ in self._search(question)]
        rerank = self._rerank(question, found) if self._reranker else None
        if rerank is not None:
            found, rerank_in, rerank_out = rerank
        else:
            rerank_in = rerank_out = 0
        self._on_step(AnswerStep(READING, count=len(found)))
        texts, dropped, left = self._fit(found, self._budget)
        if dropped:
            log.info("%d chunks did not fit the answer budget", dropped)
        if not texts:
            return Answer(
                parts=(),
                covered=False,
                sources=(),
                dropped=dropped,
                rerank_input_tokens=rerank_in,
                rerank_output_tokens=rerank_out,
            )

        if self._turns > 0:
            reply, texts, searched, turns, left = self._converse(question, texts, left)
        else:
            sources = [_source(str(n), c, t) for n, (c, t) in enumerate(texts, 1)]
            self._on_step(AnswerStep(WRITING, model=self._model.model_name))
            reply = self._model.answer(question, sources)
            searched, turns = [], 1
        kept = [chunk for chunk, _ in texts]
        by_id = {str(n): chunk for n, chunk in enumerate(kept, 1)}
        log.info(
            "%s read %d tokens and wrote %d in %d turns",
            self._model.model_name,
            reply.input_tokens,
            reply.output_tokens,
            turns,
        )
        parts = (
            tuple(_part(p.text, p.source_ids, by_id) for p in reply.passages)
            if reply.covered
            else ()
        )
        blocks = tuple(_block(b, by_id) for b in reply.blocks) if reply.covered else ()
        return Answer(
            parts=parts,
            blocks=blocks,
            covered=reply.covered,
            sources=tuple(kept),
            dropped=dropped,
            model=self._model.model_name,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            rerank_input_tokens=rerank_in,
            rerank_output_tokens=rerank_out,
            searches=tuple(searched),
            turns=turns,
        )

    def _converse(
        self, question: str, texts: list[tuple[Chunk, str]], left: int
    ) -> tuple[Reply, list[tuple[Chunk, str]], list[str], int, int]:
        """Let the model ask for searches until it answers or the turns run out.

        Return the final reply with the token counts of every turn summed,
        the chunks it read, the queries run, the turns taken, and the
        budget left. A turn that asks only for queries already run counts
        as a turn, so a model that repeats itself still stops.
        """
        searched: list[str] = []
        read_in = wrote = turns = 0
        for turns_left in range(self._turns, -1, -1):
            sources = [_source(str(n), c, t) for n, (c, t) in enumerate(texts, 1)]
            self._on_step(
                AnswerStep(
                    WRITING,
                    model=self._model.model_name,
                    turn=turns + 1,
                    turns=self._turns + 1,
                )
            )
            reply = self._model.step(question, sources, searched, turns_left)
            turns += 1
            read_in += reply.input_tokens
            wrote += reply.output_tokens
            if not reply.searches or turns_left == 0:
                break
            seen = {chunk for chunk, _ in texts}
            fresh_queries = [
                q for q in dict.fromkeys(reply.searches) if q not in searched
            ]
            for query in fresh_queries[:SEARCHES_A_TURN]:
                self._on_step(AnswerStep(FOLLOWING, query=query))
                searched.append(query)
                fresh = [c for c, _ in self._search(query) if c not in seen]
                added, _, left = self._fit(fresh[:CHUNKS_A_SEARCH], left)
                texts = texts + added
                seen.update(chunk for chunk, _ in added)
            log.info("Turn %d searched %s", turns, fresh_queries[:SEARCHES_A_TURN])
        final = Reply(
            passages=reply.passages,
            covered=reply.covered and not (reply.searches and not reply.passages),
            input_tokens=read_in,
            output_tokens=wrote,
            blocks=reply.blocks,
        )
        return final, texts, searched, turns, left

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
        self._on_step(
            AnswerStep(CHOOSING, count=len(previews), model=self._reranker.model_name)
        )
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

    def _fit(
        self, chunks: list[Chunk], left: int
    ) -> tuple[list[tuple[Chunk, str]], int, int]:
        """Keep the best chunks whose text fits *left* characters, in rank order.

        Skip a chunk that does not fit and try the next, so one long
        chunk does not push out every shorter one ranked below it. Skip a
        chunk whose text cannot be read or is empty: a model given a
        title and no text has nothing to answer from. Return the kept
        chunks with their text, how many were left out, and the budget
        left.
        """
        kept: list[tuple[Chunk, str]] = []
        for chunk in chunks:
            text = self._read(chunk)
            if not text or not text.strip():
                log.warning("Left out %s, which has no readable text", chunk.path)
                continue
            if len(text) <= left:
                kept.append((chunk, text))
                left -= len(text)
        return kept, len(chunks) - len(kept), left


def _source(source_id: str, chunk: Chunk, text: str) -> Source:
    title = f"{chunk.path}{chunk.location.render()}"
    if chunk.symbol:
        title = f"{title} {chunk.symbol}"
    return Source(id=source_id, title=title, text=text)


def _block(block: Block, by_id: dict[str, Chunk]) -> AnswerBlock:
    return AnswerBlock(
        kind=block.kind,
        heading=block.heading,
        parts=tuple(_part(p.text, p.source_ids, by_id) for p in block.passages),
        columns=block.columns,
        rows=tuple(
            AnswerRow(r.cells, _part("", r.source_ids, by_id).cites) for r in block.rows
        ),
    )


def _part(text: str, ids: Sequence[str], by_id: dict[str, Chunk]) -> AnswerPart:
    """Map cited ids to chunks, and drop an id the model was not given."""
    cites = []
    for source_id in ids:
        if source_id in by_id:
            cites.append(by_id[source_id])
        else:
            log.warning("The model cited source %r, which it was not given", source_id)
    return AnswerPart(text=text, cites=tuple(cites))
