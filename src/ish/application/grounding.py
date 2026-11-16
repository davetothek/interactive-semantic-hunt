"""Check that each claim of an answer rests on the words of a source.

A model can cite a real source and still write a claim the source does
not make. A citation alone proves nothing, so each claim carries the
words it rests on, and two checks read it. The first finds those words
in the text the model read. The second asks a model whether the words
state the claim, only imply it, or do not support it. A claim that fails
either check leaves the body of the answer, so a reader never takes it
for a fact.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from ish.application.ports.language_model import (
    IMPLIED,
    MIN_NAMED_QUOTE_WORDS,
    MIN_QUOTE_WORDS,
    STATED,
    Block,
    Claim,
    LanguageModel,
    Passage,
    Quote,
    Reply,
    Row,
)
from ish.application.ranking import identifiers_in

log = logging.getLogger(__name__)

# The words an uncited piece of an answer with native citations may hold
# and still state nothing. The text between two citations, such as "and"
# or "In short,", joins two claims. A piece with any other word, a number
# or a name among them, is a claim, so "Yes, 5 V." is checked like any.
JOINING_WORDS = frozenset(
    """a also an and as based because but documents for however in is it of
    on or short so sources that the then these this to which while with""".split()
)

# The kinds of block whose passages state facts. The gaps say what the
# sources leave open, and make no claim about the tree.
CLAIM_KINDS = ("summary", "prose", "steps", "table")

# Why a claim failed, as a reader and the model in a repair turn read it.
NO_QUOTE = "it quotes no words of a source"
SHORT_QUOTE = f"a quote has fewer than {MIN_QUOTE_WORDS} words"

# Characters of a source on each side of a quote that the check reads.
CONTEXT_CHARS = 400
QUOTE_NOT_FOUND = "a quote is not in the source it names"
OTHER_SOURCE = "a quote comes from a source the claim does not cite"
NOT_STATED = "the quoted words do not state it"


def words(text: str) -> str:
    """Return *text* as lower-case words, one space apart.

    Markup, punctuation, and line breaks differ between a source and a
    quote of it, so only the words and their order count.
    """
    return " ".join(re.findall(r"\w+", text.casefold()))


def quote_problem(quote: Quote, texts: Mapping[str, str]) -> str:
    """Return why *quote* proves nothing, or an empty text when it holds.

    The quote holds when it has ``MIN_QUOTE_WORDS`` words or more, or
    ``MIN_NAMED_QUOTE_WORDS`` when it holds a name or a number, and they
    appear, in order, in the text of the source it names.
    """
    said = words(quote.text)
    named = bool(identifiers_in(quote.text)) or any(c.isdigit() for c in quote.text)
    least = MIN_NAMED_QUOTE_WORDS if named else MIN_QUOTE_WORDS
    if len(said.split()) < least:
        return SHORT_QUOTE
    source = texts.get(quote.source_id)
    if source is None or f" {said} " not in f" {words(source)} ":
        return QUOTE_NOT_FOUND
    return ""


def context_of(quote: Quote, texts: Mapping[str, str]) -> str:
    """Return the text of the source of *quote* around it, or the quote alone.

    Find the words of the quote in order, whatever stands between them,
    and give ``CONTEXT_CHARS`` characters on each side, so a check reads
    what the quoted sentence refers to.
    """
    source = texts.get(quote.source_id, "")
    pieces = re.findall(r"\w+", quote.text)
    if not source or not pieces:
        return quote.text
    pattern = r"\W+".join(re.escape(piece) for piece in pieces)
    found = re.search(pattern, source, re.IGNORECASE)
    if found is None:
        return quote.text
    start = max(0, found.start() - CONTEXT_CHARS)
    return source[start : found.end() + CONTEXT_CHARS]


@dataclass(frozen=True, slots=True)
class Failure:
    """Hold a claim that failed a check, and why.

    Attributes:
        text: The claim. A row of a table reads as its cells, each after
            the name of its column.
        source_ids: Keys of the sources the claim cites.
        problem: Why the claim is no fact.
    """

    text: str
    source_ids: tuple[str, ...]
    problem: str


@dataclass(frozen=True, slots=True)
class Review:
    """Hold an answer with only the claims that hold, and the claims that failed.

    Attributes:
        reply: The answer, each claim of it marked ``stated`` or
            ``implied``. Not covered when no claim holds.
        failures: The claims that failed, in reading order.
        claims: Claims the second check read.
        input_tokens: Tokens the checking model read.
        output_tokens: Tokens the checking model wrote.
        cost_usd: What the backend says the check cost, in US dollars.
    """

    reply: Reply
    failures: tuple[Failure, ...] = ()
    claims: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class _Item:
    """Hold one passage or row while the checks read it."""

    text: str
    source_ids: tuple[str, ...]
    quotes: tuple[Quote, ...]
    claim: bool
    basis: str = ""
    problem: str = ""


def review(reply: Reply, texts: Mapping[str, str], judge: LanguageModel) -> Review:
    """Check each claim of *reply* against *texts*, the sources by key.

    Find each quote in its source first, which costs nothing. Ask *judge*
    in one call about the claims whose quotes hold. Keep a claim that a
    quote states or implies, marked so. Take out a claim that fails, with
    the reason. A short passage that cites nothing joins two claims and
    stays as it is, unless its block keeps no claim at all. An answer
    with a layout has no such pieces: each of its passages and rows is a
    claim.
    """
    if not reply.covered:
        return Review(reply)
    laid_out = bool(reply.blocks)
    blocks = reply.blocks or (Block(kind="prose", passages=reply.passages),)
    items: dict[tuple[int, str, int], _Item] = {}
    for b, block in enumerate(blocks):
        if block.kind not in CLAIM_KINDS:
            continue
        for p, passage in enumerate(block.passages):
            items[(b, "p", p)] = _item(
                passage.text, passage.source_ids, passage.quotes, laid_out
            )
        for r, row in enumerate(block.rows):
            items[(b, "r", r)] = _item(
                _row_text(block.columns, row), row.source_ids, row.quotes, True
            )
    for item in items.values():
        if item.claim:
            item.problem = _quotes_problem(item, texts)
    asked = [item for item in items.values() if item.claim and not item.problem]
    verdicts = None
    if asked:
        claims = [
            Claim(
                str(n),
                item.text,
                tuple(q.text for q in item.quotes),
                tuple(context_of(q, texts) for q in item.quotes),
            )
            for n, item in enumerate(asked, 1)
        ]
        verdicts = judge.check(claims)
        for item, verdict in zip(asked, verdicts.verdicts, strict=True):
            if verdict in (STATED, IMPLIED):
                item.basis = verdict
            else:
                item.problem = NOT_STATED
    kept = tuple(
        found
        for b, block in enumerate(blocks)
        if (found := _kept(b, block, items)) is not None
    )
    held = any(item.basis for item in items.values())
    failures = tuple(
        Failure(item.text, item.source_ids, item.problem)
        for item in items.values()
        if item.problem
    )
    if failures:
        log.info("%d claims of the answer failed the check", len(failures))
    checked = replace(
        reply,
        passages=tuple(_flat(kept)) if held else (),
        blocks=kept if reply.blocks and held else (),
        covered=held,
    )
    return Review(
        reply=checked,
        failures=failures,
        claims=len(asked),
        input_tokens=verdicts.input_tokens if verdicts else 0,
        output_tokens=verdicts.output_tokens if verdicts else 0,
        cost_usd=verdicts.cost_usd if verdicts else 0.0,
    )


def _item(
    text: str, source_ids: Sequence[str], quotes: Sequence[Quote], claim: bool
) -> _Item:
    """Return one passage or row to check, and say whether it is a claim.

    A piece that is no claim by its place is still one when it cites a
    source or holds a word outside ``JOINING_WORDS``.
    """
    stated = set(words(text).split()) - JOINING_WORDS
    claim = claim or bool(source_ids) or bool(stated)
    return _Item(text, tuple(source_ids), tuple(quotes), claim)


def _row_text(columns: Sequence[str], row: Row) -> str:
    """Return a row as one claim, each cell after the name of its column."""
    named = [
        f"{columns[n]}: {cell}" if n < len(columns) and columns[n] else cell
        for n, cell in enumerate(row.cells)
    ]
    return "; ".join(named)


def _quotes_problem(item: _Item, texts: Mapping[str, str]) -> str:
    """Return why the quotes of *item* prove nothing, or an empty text.

    Every quote must hold. A claim with one quote that is not in its
    source is no claim a reader can trust, even beside a good one.
    """
    if not item.quotes:
        return NO_QUOTE
    for quote in item.quotes:
        # A reader checks a claim against the sources it cites, so a quote
        # of any other source proves the claim to no reader.
        if quote.source_id not in item.source_ids:
            return OTHER_SOURCE
        problem = quote_problem(quote, texts)
        if problem:
            return problem
    return ""


def _kept(
    b: int, block: Block, items: Mapping[tuple[int, str, int], _Item]
) -> Block | None:
    """Return *block* with the claims that hold, or None when it keeps none.

    Mark each claim that holds with its basis. Keep a passage that joins
    two claims only beside a claim that holds. A table that keeps no row
    becomes prose.
    """
    if block.kind not in CLAIM_KINDS:
        return block
    passages = []
    for p, passage in enumerate(block.passages):
        item = items[(b, "p", p)]
        if not item.problem:
            passages.append(replace(passage, basis=item.basis))
    rows = tuple(
        replace(row, basis=items[(b, "r", r)].basis)
        for r, row in enumerate(block.rows)
        if not items[(b, "r", r)].problem
    )
    if not any(p.basis for p in passages) and not rows:
        return None
    kind = "prose" if block.kind == "table" and not rows else block.kind
    return replace(block, kind=kind, passages=tuple(passages), rows=rows)


def _flat(blocks: Sequence[Block]) -> list[Passage]:
    """Return every passage and every table row, as passages, in reading order."""
    flat: list[Passage] = []
    for block in blocks:
        flat.extend(block.passages)
        flat.extend(
            Passage(" | ".join(r.cells), r.source_ids, r.quotes, r.basis)
            for r in block.rows
        )
    return flat


def repair_note(failures: Sequence[Failure]) -> str:
    """Return the note that asks the model to fix the claims that failed."""
    listed = "\n".join(f'- "{f.text.strip()}": {f.problem}.' for f in failures)
    return (
        "A check of your answer found claims that no quote supports. For each "
        "claim below, quote the words of a source that state it, search for a "
        "source that states it, or leave the claim out. Do not write a claim "
        "again without a quote that states it. Put a point that the sources "
        f"leave unconfirmed in gaps.\n\n{listed}"
    )


def challenge_note(reply: Reply, searches: int) -> str:
    """Return the note that asks the model to search for evidence against *reply*."""
    claims = [p.text.strip() for p in reply.passages if p.text.strip()][:5]
    listed = "\n".join(f"- {c}" for c in claims)
    return (
        "Do not answer yet. Your draft answer says:\n\n"
        f"{listed}\n\n"
        f"Ask for up to {searches} searches that could find a source that "
        "disagrees with the draft, limits it, or names a condition it leaves "
        "out. Write each query about the subject, not about the draft."
    )


CHALLENGE_DONE = (
    "The searches for evidence against your draft have run. Answer from all "
    "the sources now. Put each point where the sources disagree in gaps."
)
