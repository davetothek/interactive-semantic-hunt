"""Implement the answer use case — search, then write an answer from what it found.

The language model reads only the chunks the search returned. Each part
of the answer cites the chunks that support it, so a reader can open the
source of every claim. A question the chunks do not cover gets no answer
and the closest chunks instead, never an answer from outside them.
"""

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ish.application.glossary import Glossary, acronyms
from ish.application.ports.language_model import (
    SEARCHES_A_TURN,
    Block,
    LanguageModel,
    Reply,
    Source,
    Term,
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

# Acronyms an answer names at most. A register table can hold dozens of
# names, and a reader wants the terms, not a second table.
MAX_TERMS = 20

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
class AnswerTerm:
    """Hold an acronym the answer uses, what it stands for, and where it says so."""

    term: str
    meaning: str
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
        rerank_model: Name of the model that chose the chunks, or empty
            when none ran.
        timings: Seconds spent in each stage, by stage, in the order the
            stages first ran: ``searching``, ``reading``, ``choosing``,
            ``writing``, and ``following``. Reading counts every chunk read
            from its file, for the rerank and for the answer.
        seconds: Seconds the whole answer took.
        terms: The acronyms the answer uses, each with what it stands for
            and the sources that write it, sorted by term. A meaning that
            no source writes is not kept, so none is invented.
        cost_usd: What the backends say the rerank and every turn of the
            answer cost, in US dollars, or 0 when they do not say.
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
    rerank_model: str = ""
    timings: tuple[tuple[str, float], ...] = ()
    seconds: float = 0.0
    terms: tuple[AnswerTerm, ...] = ()
    cost_usd: float = 0.0


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
        clock: Callable[[], float] = time.monotonic,
        glossary: Glossary | None = None,
        kind_of: Callable[[Chunk], str] | None = None,
        on_block: Callable[["AnswerBlock"], None] | None = None,
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

        Time each stage on *clock*, so a reader of the answer sees where
        its seconds went.

        Add every text read to *glossary*, and name each acronym of the
        answer that the model did not spell out with the meaning the
        glossary holds. A session shares one glossary between questions,
        and a server fills it from its whole tree.

        Name the type of each source in its title through *kind_of*, so
        the model can tell a specification from a test or code. Read one
        copy of a text that several chunks hold, and name the others in
        its title.

        Give *on_block* each block of the answer as the model writes it,
        with its citations as chunks, when the backend streams. The
        answer holds every block again, and a block of a turn that then
        asks for a search is no part of the answer.
        """
        self._search = search
        self._model = model
        self._budget = budget_chars
        self._read = read
        self._reranker = reranker
        self._pick = pick
        self._turns = turns
        self._on_step = on_step
        self._clock = clock
        self._spent: dict[str, float] = {}
        self._glossary = Glossary() if glossary is None else glossary
        self._kind_of = kind_of
        self._on_block = on_block
        # What one answer has read, so no chunk is read twice. Each text
        # is keyed by its words alone, to find a copy in another file.
        self._texts: dict[Chunk, str | None] = {}
        self._first: dict[str, Chunk] = {}
        self._copies: dict[Chunk, list[Chunk]] = {}
        self._rerank_cost = 0.0

    def _timed(
        self, stage: str, call: Callable[..., object], *args: object, **named: object
    ):
        """Run *call* with *args*, and add the seconds it took to *stage*."""
        start = self._clock()
        try:
            return call(*args, **named)
        finally:
            self._spent[stage] = self._spent.get(stage, 0.0) + self._clock() - start

    def answer(self, question: str) -> Answer:
        """Answer *question* from the chunks a search returns, and time it.

        Raise ``ValueError`` for an empty question. Ask the model nothing
        when the search finds nothing.
        """
        self._spent = {}
        self._texts, self._first, self._copies = {}, {}, {}
        self._rerank_cost = 0.0
        start = self._clock()
        answer = self._answer(question)
        return _with_time(answer, tuple(self._spent.items()), self._clock() - start)

    def _answer(self, question: str) -> Answer:
        """Search, choose, read, and ask the model, in that order."""
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        self._on_step(AnswerStep(SEARCHING))
        found = [chunk for chunk, _ in self._timed(SEARCHING, self._search, question)]
        found = self._distinct(found)
        rerank = self._rerank(question, found) if self._reranker else None
        if rerank is not None:
            found, rerank_in, rerank_out = rerank
        else:
            rerank_in = rerank_out = 0
        rerank_model = (
            self._reranker.model_name if self._reranker and rerank is not None else ""
        )
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
                rerank_model=rerank_model,
                cost_usd=self._rerank_cost,
            )

        if self._turns > 0:
            reply, texts, searched, turns, left = self._converse(question, texts, left)
        else:
            sources = [self._titled(str(n), c, t) for n, (c, t) in enumerate(texts, 1)]
            self._on_step(AnswerStep(WRITING, model=self._model.model_name))
            reply = self._timed(
                WRITING,
                self._model.answer,
                question,
                sources,
                **self._streaming(texts),
            )
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
        terms = self._named(reply, texts, kept) if reply.covered else ()
        return Answer(
            parts=parts,
            blocks=blocks,
            terms=terms,
            covered=reply.covered,
            sources=tuple(kept),
            dropped=dropped,
            model=self._model.model_name,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            rerank_input_tokens=rerank_in,
            rerank_output_tokens=rerank_out,
            rerank_model=rerank_model,
            searches=tuple(searched),
            turns=turns,
            cost_usd=reply.cost_usd + self._rerank_cost,
        )

    def _named(
        self, reply: Reply, texts: list[tuple[Chunk, str]], kept: list[Chunk]
    ) -> tuple[AnswerTerm, ...]:
        """Return the acronyms of the answer, each with a meaning a source writes.

        Keep the terms the model spelled out from the sources it read, then
        look up the rest of the acronyms the answer uses in the glossary,
        nearest to the sources the answer read. Sort them by term.
        """
        words = " ".join(_words(reply))
        named = {t.term: t for t in _terms(reply.terms, texts, words)}
        for short in acronyms(words):
            if short in named or len(named) >= MAX_TERMS:
                continue
            found = self._glossary.lookup(short, kept)
            if found is not None:
                named[short] = AnswerTerm(short, found.meaning, (found.chunk,))
        return tuple(named[t] for t in sorted(named, key=str.casefold))

    def _streaming(self, texts: list[tuple[Chunk, str]]) -> dict[str, object]:
        """Return what asks the model to stream, with the sources of *texts*.

        Map the ids of a streamed block to its chunks as the answer maps
        them, so a block reads the same streamed and in the answer. Ask a
        model nothing more when no one takes the blocks.
        """
        if self._on_block is None:
            return {}
        by_id = {str(n): chunk for n, (chunk, _) in enumerate(texts, 1)}
        give = self._on_block
        return {"on_block": lambda block: give(_block(block, by_id))}

    def _read_kept(self, chunk: Chunk) -> str | None:
        """Read *chunk* once, and add what its text defines to the glossary."""
        if chunk in self._texts:
            return self._texts[chunk]
        text = self._timed(READING, self._read, chunk)
        self._glossary.add(chunk, text)
        self._texts[chunk] = text
        return text

    def _distinct(self, chunks: Sequence[Chunk]) -> list[Chunk]:
        """Keep the first chunk of each text, and note the others as its copies.

        A tree can hold one section in several files, word for word. Each
        copy would take a place among the candidates and say nothing new.
        Compare the words alone, so a change of spacing or case is still
        a copy. Keep a chunk with no readable text: the budget leaves it
        out later and says so.
        """
        kept: list[Chunk] = []
        for chunk in chunks:
            text = self._read_kept(chunk)
            key = " ".join(text.split()).casefold() if text else ""
            first = self._first.setdefault(key, chunk) if key else chunk
            if first is chunk:
                kept.append(chunk)
            else:
                self._copies.setdefault(first, []).append(chunk)
        return kept

    def _titled(self, source_id: str, chunk: Chunk, text: str) -> Source:
        """Return *chunk* as a source the model reads, with its type and copies."""
        title = f"{chunk.path}{chunk.location.render()}"
        if chunk.symbol:
            title = f"{title} {chunk.symbol}"
        if self._kind_of is not None:
            title = f"{self._kind_of(chunk)}: {title}"
        copies = self._copies.get(chunk)
        if copies:
            places = ", ".join(f"{c.path}{c.location.render()}" for c in copies)
            title = f"{title} (the same text is also in {places})"
        return Source(id=source_id, title=title, text=text)

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
        cost = 0.0
        for turns_left in range(self._turns, -1, -1):
            sources = [self._titled(str(n), c, t) for n, (c, t) in enumerate(texts, 1)]
            self._on_step(
                AnswerStep(
                    WRITING,
                    model=self._model.model_name,
                    turn=turns + 1,
                    turns=self._turns + 1,
                )
            )
            reply = self._timed(
                WRITING,
                self._model.step,
                question,
                sources,
                searched,
                turns_left,
                **self._streaming(texts),
            )
            turns += 1
            read_in += reply.input_tokens
            wrote += reply.output_tokens
            cost += reply.cost_usd
            if not reply.searches or turns_left == 0:
                break
            seen = {chunk for chunk, _ in texts}
            fresh_queries = [
                q for q in dict.fromkeys(reply.searches) if q not in searched
            ]
            for query in fresh_queries[:SEARCHES_A_TURN]:
                self._on_step(AnswerStep(FOLLOWING, query=query))
                searched.append(query)
                fresh = self._distinct(
                    [
                        c
                        for c, _ in self._timed(FOLLOWING, self._search, query)
                        if c not in seen
                    ]
                )
                added, _, left = self._fit(fresh[:CHUNKS_A_SEARCH], left)
                texts = texts + added
                seen.update(chunk for chunk, _ in added)
            log.info("Turn %d searched %s", turns, fresh_queries[:SEARCHES_A_TURN])
        final = Reply(
            passages=reply.passages,
            covered=reply.covered and not (reply.searches and not reply.passages),
            input_tokens=read_in,
            output_tokens=wrote,
            cost_usd=cost,
            blocks=reply.blocks,
            terms=reply.terms,
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
            text = self._read_kept(chunk)
            if not text or not text.strip():
                continue
            start = " ".join(text.split())[:PREVIEW_CHARS]
            previews.append(self._titled(str(n), chunk, start))
            by_id[str(n)] = chunk
        if not previews:
            return None
        self._on_step(
            AnswerStep(CHOOSING, count=len(previews), model=self._reranker.model_name)
        )
        selection = self._timed(
            CHOOSING, self._reranker.select, question, previews, self._pick
        )
        chosen = [by_id[i] for i in selection.ids if i in by_id]
        self._rerank_cost = selection.cost_usd
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
            text = self._read_kept(chunk)
            if not text or not text.strip():
                log.warning("Left out %s, which has no readable text", chunk.path)
                continue
            if len(text) <= left:
                kept.append((chunk, text))
                left -= len(text)
        return kept, len(chunks) - len(kept), left


def _with_time(
    answer: Answer, timings: tuple[tuple[str, float], ...], seconds: float
) -> Answer:
    """Return *answer* with the seconds of each stage and of the whole."""
    from dataclasses import replace

    return replace(answer, timings=timings, seconds=seconds)


def _words(reply: Reply) -> list[str]:
    """Return every text of a reply a reader sees: passages and table cells."""
    words = [p.text for p in reply.passages]
    for block in reply.blocks:
        words += [cell for row in block.rows for cell in row.cells]
        words += list(block.columns)
    return words


def _plain(text: str) -> str:
    """Return *text* with its spacing and case evened, to compare by words."""
    return " ".join(text.split()).casefold()


def _terms(
    found: Sequence[Term], texts: list[tuple[Chunk, str]], answer: str
) -> tuple[AnswerTerm, ...]:
    """Keep each term the answer uses whose meaning a source writes.

    Cite the sources the model named that write the meaning. When none of
    them does, cite the first source sent that writes it, because the
    meaning came from the results either way. Drop a term the answer
    does not use, a meaning that only repeats the term, and a meaning no
    source writes, so a model that expands a term from its own knowledge
    adds nothing. Keep one entry for each term, sorted by term.
    """
    by_id = {str(n): pair for n, pair in enumerate(texts, 1)}
    kept: dict[str, AnswerTerm] = {}
    dropped = 0
    for term in found:
        name, meaning = term.term.strip(), term.meaning.strip()
        used = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", answer) if name else None
        if not used or not meaning or _plain(meaning) == _plain(name):
            dropped += 1
            continue
        writes = [
            chunk
            for i in term.source_ids
            if i in by_id and _plain(meaning) in _plain(by_id[i][1])
            for chunk in (by_id[i][0],)
        ]
        if not writes:
            writes = next(
                ([c] for c, text in texts if _plain(meaning) in _plain(text)), []
            )
        if not writes:
            dropped += 1
            continue
        kept.setdefault(name, AnswerTerm(name, meaning, tuple(writes)))
    if dropped:
        log.info("Left out %d terms that no source spells out", dropped)
    return tuple(kept[name] for name in sorted(kept, key=str.casefold))


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
