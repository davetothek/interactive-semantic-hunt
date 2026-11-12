"""Chat use case: a conversation about the files of an index.

Each turn of a chat is one answer of the answer use case, from the chunks
a search finds, with a citation for each claim. The chat keeps what was
asked and answered while it lasts, and gives it to the model as context
for reading the next question. The history is never a source: the model
reads it under a heading that says so, and a claim still needs a chunk.

No model rewrites a question or summarizes the history. A follow-up such
as "and its reset value?" is searched alone and together with the
question before it, and the chunks the last answer cited are offered
again beside the results. Past its budget, the history keeps the recent
turns whole and only the questions of the older ones.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ish.application.answer import Answer
from ish.application.ports.language_model import LanguageModel
from ish.domain.chunk import Chunk
from ish.domain.match import Match

# The history a prompt holds, against the 9,000 to 16,000 tokens of
# sources that an answer reads, so the sources stay most of the prompt.
HISTORY_TOKENS = 4_000

# How many characters make a token, to count the history without a model.
CHARS_PER_TOKEN = 4

# What the model reads above the history, so it reads the turns as context.
HISTORY_HEADING = (
    "This is a chat. The turns below are what was asked and answered "
    "before. Read the question in their light. They are no source: answer "
    "only from the sources, and cite them."
)

# What a turn says of an answer that the sources did not cover.
NOT_COVERED = "The sources do not cover it."


@dataclass(frozen=True, slots=True)
class Reading:
    """How one answer of a chat finds its sources.

    Run each of *queries*, and offer the chunks of *carry* beside the
    results. With *only*, read those chunks and nothing else: no search
    and no rerank.
    """

    queries: tuple[str, ...] = ()
    carry: tuple[Chunk, ...] = ()
    only: tuple[Chunk, ...] = ()


@dataclass(frozen=True, slots=True)
class Turn:
    """One question of a chat, its answer, and the text the model read."""

    question: str
    answer: Answer
    asked: str
    brief: bool


# Answer the text the model reads, find the sources as the reading says,
# and write the short answer when the flag is set.
Ask = Callable[[str, Reading, bool], Answer]


class Chat:
    """Hold one conversation, and answer each question of it."""

    def __init__(
        self, ask: Ask, *, brief: bool = True, budget_tokens: int = HISTORY_TOKENS
    ) -> None:
        """Answer through *ask*, briefly unless *brief* is False."""
        self._ask = ask
        self.brief = brief
        self._budget = budget_tokens * CHARS_PER_TOKEN
        self._turns: list[Turn] = []

    @property
    def last(self) -> Turn | None:
        """Return the last turn, or None before the first answer."""
        return self._turns[-1] if self._turns else None

    @property
    def turns(self) -> tuple[Turn, ...]:
        """Return every turn of the chat, oldest first."""
        return tuple(self._turns)

    def ask(self, question: str) -> Turn:
        """Answer *question* in the light of the chat so far.

        Search for the question alone, and with the question before it,
        and offer the chunks the last answer cited beside the results.
        Raise ``ValueError`` for an empty question.
        """
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        previous = self.last
        queries = (question,)
        carry: tuple[Chunk, ...] = ()
        if previous is not None:
            queries = (question, f"{previous.question} {question}")
            carry = cited(previous.answer)
        asked = self._with_history(question)
        answer = self._ask(asked, Reading(queries, carry), self.brief)
        turn = Turn(question, answer, asked, self.brief)
        self._turns.append(turn)
        return turn

    def more(self) -> Turn:
        """Write the full answer of the last question, from what it read.

        Run no search and no rerank: the chunks are those the last answer
        read. The full answer takes the place of the last turn, so the
        history holds the longer text. Raise ``LookupError`` before the
        first answer.
        """
        last = self.last
        if last is None:
            raise LookupError("There is no answer yet. Ask a question first.")
        answer = self._ask(last.asked, Reading(only=last.answer.sources), False)
        turn = Turn(last.question, answer, last.asked, False)
        self._turns[-1] = turn
        return turn

    def new(self) -> None:
        """Forget the history, as if the chat started now."""
        self._turns.clear()

    def _with_history(self, question: str) -> str:
        """Return *question* with the history the budget holds above it."""
        history = history_text(self._turns, self._budget)
        if not history:
            return question
        return f"{HISTORY_HEADING}\n\n{history}\n\nQuestion: {question}"


def cited(answer: Answer) -> tuple[Chunk, ...]:
    """Return each chunk *answer* cites, once, in the order it cites them."""
    found: dict[Chunk, None] = {}
    for part in answer.parts:
        for chunk in part.cites:
            found.setdefault(chunk, None)
    return tuple(found)


def said(answer: Answer) -> str:
    """Return what *answer* says, as plain text with no markers."""
    if not answer.covered:
        return NOT_COVERED
    return " ".join(part.text.strip() for part in answer.parts if part.text.strip())


def history_text(turns: Sequence[Turn], budget_chars: int) -> str:
    """Return the history of *turns* that fits *budget_chars*.

    Keep the newest turns whole while they fit, and the newest of all
    even when it alone does not. Of the turns before them, keep the
    questions alone, the newest while they fit. Return the text oldest
    first.
    """
    whole: list[str] = []
    used = 0
    # The turns before this index give their question alone.
    cut = 0
    for index in range(len(turns) - 1, -1, -1):
        text = f"Q: {turns[index].question}\nA: {said(turns[index].answer)}"
        if whole and used + len(text) > budget_chars:
            cut = index + 1
            break
        whole.append(text)
        used += len(text)
    asked: list[str] = []
    for turn in reversed(turns[:cut]):
        cost = len(turn.question) + len(" | ")
        if used + cost > budget_chars:
            break
        asked.append(turn.question)
        used += cost
    blocks = []
    if asked:
        blocks.append("Earlier questions: " + " | ".join(reversed(asked)))
    blocks.extend(reversed(whole))
    return "\n\n".join(blocks)


def merged(
    results: Sequence[Sequence[Match]], carry: Sequence[Chunk], limit: int
) -> list[Match]:
    """Return the results of several searches as one list, and *carry* after it.

    Keep each chunk once, with its best score, and the best *limit* of
    them by score. The searches read one index with one model, so their
    scores compare. Add each chunk of *carry* that is not there, with a
    score of 0, so a rerank still sees it.
    """
    best: dict[Chunk, float] = {}
    for found in results:
        for chunk, score in found:
            if chunk not in best or score > best[chunk]:
                best[chunk] = score
    ranked = sorted(best.items(), key=lambda pair: pair[1], reverse=True)[:limit]
    kept = [Match(chunk, score) for chunk, score in ranked]
    present = {match.chunk for match in kept}
    kept.extend(Match(chunk, 0.0) for chunk in carry if chunk not in present)
    return kept


def plan(
    reading: Reading,
    search: Callable[[str], Sequence[Match]],
    limit: int,
    reranker: LanguageModel | None,
    turns: int | None,
) -> tuple[Callable[[str], Sequence[Match]], LanguageModel | None, int | None]:
    """Return the search, the rerank model, and the turns of one answer of a chat.

    With the ``only`` of *reading*, return a search that gives those
    chunks, with no rerank model and no follow-up search. Else return a
    search whose first call runs the queries of the reading, merged, and
    adds its ``carry``. Its later calls are the follow-up searches the
    model asks for, and run as given.
    """
    if reading.only:
        chosen = [Match(chunk, 1.0) for chunk in reading.only]
        return lambda _text: chosen, None, 0
    first = [True]

    def found(text: str) -> Sequence[Match]:
        if not first[0]:
            return search(text)
        first[0] = False
        return merged(
            [search(query) for query in reading.queries], reading.carry, limit
        )

    return found, reranker, turns
