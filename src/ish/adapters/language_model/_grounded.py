"""Ask for an answer with citations from a model that has no citation feature.

The model reads the sources, each under its id, and replies in JSON that
a schema fixes: whether the sources cover the question, a short answer,
sections of prose, steps, or tables, and what the sources leave open.
Every passage and every table row names the ids it rests on. The use
case drops an id it did not send, so a model that invents a citation
cites nothing. The acronyms the answer uses come with what they stand
for and the ids of the sources that write it.
"""

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from ish.adapters.language_model._json_stream import JsonValues
from ish.application.ports.language_model import (
    UNSUPPORTED,
    VERDICTS,
    Block,
    Claim,
    LanguageModelError,
    Passage,
    Query,
    Quote,
    Reply,
    Row,
    Selection,
    Source,
    Term,
    Verdicts,
)

# Points of what the sources leave open that an answer keeps. The answer
# instruction asks for at most this many, and the parser keeps no more.
MAX_GAPS = 5

# The words of a source that a claim rests on, copied from it.
QUOTE: dict[str, object] = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string"},
        "text": {"type": "string"},
    },
    "required": ["source_id", "text"],
    "additionalProperties": False,
}

QUOTES: dict[str, object] = {"type": "array", "items": QUOTE}

PASSAGE: dict[str, object] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
        "quotes": QUOTES,
    },
    "required": ["text", "source_ids", "quotes"],
    "additionalProperties": False,
}

PASSAGES: dict[str, object] = {"type": "array", "items": PASSAGE}

SECTION: dict[str, object] = {
    "type": "object",
    "properties": {
        "heading": {"type": "string"},
        "style": {"type": "string", "enum": ["prose", "steps", "table"]},
        "passages": PASSAGES,
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cells": {"type": "array", "items": {"type": "string"}},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "quotes": QUOTES,
                },
                "required": ["cells", "source_ids", "quotes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["heading", "style", "passages", "columns", "rows"],
    "additionalProperties": False,
}

TERM: dict[str, object] = {
    "type": "object",
    "properties": {
        "term": {"type": "string"},
        "meaning": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["term", "meaning", "source_ids"],
    "additionalProperties": False,
}

ANSWER_PROPERTIES: dict[str, object] = {
    "covered": {"type": "boolean"},
    "summary": PASSAGE,
    "sections": {"type": "array", "items": SECTION},
    "gaps": PASSAGES,
    "terms": {"type": "array", "items": TERM},
}

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": ANSWER_PROPERTIES,
    "required": ["covered", "summary", "sections", "gaps", "terms"],
    "additionalProperties": False,
}


def prompt(question: str, sources: Sequence[Source]) -> str:
    """Return the user message: every source under its id, then the question."""
    blocks = [f"[{s.id}] {s.title}\n{s.text}" for s in sources]
    return "\n\n".join([*blocks, f"Question: {question}"])


def parse(
    text: str, backend: str, input_tokens: int = 0, output_tokens: int = 0
) -> Reply:
    """Turn the JSON a model wrote into a reply with its layout.

    Lay the passages out flat as well, in the order a reader meets them:
    the summary, each section with its steps and table rows, then the
    gaps. Raise ``LanguageModelError`` naming *backend* when the text is
    not the JSON the schema asks for.
    """
    try:
        data = json.loads(text)
        covered = data["covered"]
        blocks = _blocks(data)
        terms = tuple(_term(t) for t in data.get("terms") or ())
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise LanguageModelError(
            f"{backend} did not reply in the JSON the answer needs: {exc}"
        ) from exc
    if not isinstance(covered, bool):
        raise LanguageModelError(f"{backend} replied with covered={covered!r}.")
    passages = tuple(_flat(blocks))
    return Reply(
        passages=passages,
        covered=covered and bool(passages),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        blocks=blocks,
        terms=terms,
    )


def _term(data: dict) -> Term:
    """Read one term as the model wrote it. The use case checks its meaning."""
    return Term(
        term=str(data["term"]).strip(),
        meaning=str(data["meaning"]).strip(),
        source_ids=tuple(str(i) for i in data.get("source_ids") or ()),
    )


# A marker such as [16] that a model wrote into its own text. It names a
# source by the id the model saw, which is not the number a reader sees.
_OWN_MARKER = re.compile(r" ?\[\d+\]")


def _passage(data: dict) -> Passage:
    """Read one passage, and drop any citation marker written into its text.

    ish numbers the citations itself, from the ids a passage lists.
    """
    return Passage(
        text=_OWN_MARKER.sub("", str(data["text"])),
        source_ids=tuple(str(i) for i in data.get("source_ids") or ()),
        quotes=_quotes(data),
    )


def _quotes(data: dict) -> tuple[Quote, ...]:
    """Read the quotes of a passage or a row, and drop an empty one."""
    found = (
        Quote(str(q["source_id"]), str(q["text"])) for q in data.get("quotes") or ()
    )
    return tuple(q for q in found if q.text.strip())


def _blocks(data: dict) -> tuple[Block, ...]:
    """Read the summary, the sections, and the gaps, and drop what is empty."""
    found = [
        _summary_block(data.get("summary")),
        *(_section_block(section) for section in data.get("sections") or ()),
        _gaps_block(data.get("gaps")),
    ]
    return tuple(block for block in found if block is not None)


def _summary_block(summary: dict | None) -> Block | None:
    """Read the summary, or None when it is empty."""
    if summary and str(summary.get("text") or "").strip():
        return Block(kind="summary", passages=(_passage(summary),))
    return None


def _section_block(section: dict) -> Block | None:
    """Read one section in its style, or None when it holds nothing."""
    style = str(section.get("style") or "prose")
    kind = style if style in ("prose", "steps", "table") else "prose"
    rows = tuple(
        Row(
            cells=tuple(_OWN_MARKER.sub("", str(c)) for c in row["cells"]),
            source_ids=tuple(str(i) for i in row.get("source_ids") or ()),
            quotes=_quotes(row),
        )
        for row in section.get("rows") or ()
    )
    passages = tuple(
        _passage(p) for p in section.get("passages") or () if str(p["text"]).strip()
    )
    if kind == "table" and not rows:
        kind = "prose"
    if not (passages or rows):
        return None
    return Block(
        kind=kind,
        heading=str(section.get("heading") or "").strip(),
        passages=passages,
        columns=tuple(str(c) for c in section.get("columns") or ()),
        rows=rows if kind == "table" else (),
    )


def _gaps_block(gaps: list | None) -> Block | None:
    """Read the points the sources leave open, at most ``MAX_GAPS``, or None."""
    kept = tuple(_passage(p) for p in gaps or () if str(p["text"]).strip())
    return Block(kind="gaps", passages=kept[:MAX_GAPS]) if kept else None


class BlockStream:
    """Turn the JSON of an answer, as it arrives, into its blocks in reading order.

    Give *on_block* each block as soon as its JSON is whole: the summary,
    each section, then the gaps, built as ``parse`` builds them. A model
    that writes the parts in another order streams nothing more, so a
    reader never sees a block in a place the whole answer would not put
    it. The whole reply is parsed as before when it ends.
    """

    def __init__(self, on_block: Callable[[Block], None]) -> None:
        self._values = JsonValues()
        self._on_block = on_block
        # The part the reading order expects next, or "" once it stopped.
        self._next = "summary"

    def feed(self, piece: str) -> None:
        """Add *piece* of the JSON, and give on each block it completes."""
        for path, value in self._values.feed(piece):
            if self._next:
                self._take(path, value)

    def _take(self, path: tuple[str | int, ...], value: Any) -> None:
        """Give on the block that *value* at *path* completes, if it is due."""
        part = path[0] if path else ""
        if part not in ("summary", "sections", "gaps") or len(path) > 2:
            return
        if len(path) == 2 and not (part == "sections" and isinstance(path[1], int)):
            return
        due = {"summary": "summary", "sections": "sections", "gaps": "gaps"}[part]
        if self._next != due:
            self._next = ""
            return
        try:
            if part == "summary":
                block = _summary_block(value)
                self._next = "sections"
            elif len(path) == 2:
                block = _section_block(value)
            elif part == "sections":
                self._next, block = "gaps", None
            else:
                block = _gaps_block(value)
                self._next = "done"
        except (KeyError, TypeError, AttributeError):
            # The whole reply is parsed again at its end, and says what is wrong.
            self._next = ""
            return
        if block is not None:
            self._on_block(block)


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


SELECT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["ids"],
    "additionalProperties": False,
}


QUERY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}


def query_prompt(question: str, history: str) -> str:
    """Return the user message that asks for one search query."""
    return f"Earlier turns:\n\n{history}\n\nNew question: {question}"


def parse_query(
    text: str, backend: str, input_tokens: int = 0, output_tokens: int = 0
) -> Query:
    """Turn the JSON a model wrote into a search query.

    Raise ``LanguageModelError`` naming *backend* when the text is not the
    JSON the schema asks for, or when the query is empty.
    """
    try:
        query = " ".join(str(json.loads(text)["query"]).split())
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LanguageModelError(
            f"{backend} did not reply with a search query: {exc}"
        ) from exc
    if not query:
        raise LanguageModelError(f"{backend} wrote an empty search query.")
    return Query(query, input_tokens, output_tokens)


def select_prompt(question: str, candidates: Sequence[Source], limit: int) -> str:
    """Return the user message that asks for at most *limit* candidate ids."""
    blocks = [f"[{c.id}] {c.title}\n{c.text}" for c in candidates]
    return "\n\n".join([*blocks, f"Choose at most {limit}.", f"Question: {question}"])


def parse_ids(
    text: str, backend: str, limit: int, input_tokens: int = 0, output_tokens: int = 0
) -> Selection:
    """Turn the JSON a model wrote into at most *limit* ids, without repeats.

    Raise ``LanguageModelError`` naming *backend* when the text is not the
    JSON the schema asks for.
    """
    try:
        ids = [str(i) for i in json.loads(text)["ids"]]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LanguageModelError(
            f"{backend} did not reply with the ids to read: {exc}"
        ) from exc
    return Selection(
        ids=tuple(dict.fromkeys(ids))[:limit],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


STEP_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        **ANSWER_PROPERTIES,
        "search": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["covered", "summary", "sections", "gaps", "terms", "search"],
    "additionalProperties": False,
}


def step_prompt(
    question: str,
    sources: Sequence[Source],
    searched: Sequence[str],
    turns_left: int,
    notes: Sequence[str] = (),
) -> str:
    """Return the user message of one turn: the sources, what ran, what is left.

    Add the *notes* of ish last, so the model reads them just before it
    replies.
    """
    ran = "\n".join(f"- {q}" for q in searched) or "- none"
    parts = [
        prompt(question, sources),
        f"Searches already run:\n{ran}",
        f"Turns left for searches: {turns_left}",
    ]
    if notes:
        parts.append("Notes from ish:\n\n" + "\n\n".join(notes))
    return "\n\n".join(parts)


def parse_step(
    text: str, backend: str, input_tokens: int = 0, output_tokens: int = 0
) -> Reply:
    """Turn the JSON of one turn into a reply, with any searches it asks for."""
    reply = parse(text, backend, input_tokens, output_tokens)
    # parse has already refused a reply that is not a JSON object.
    searches = tuple(str(q).strip() for q in json.loads(text).get("search") or ())
    return Reply(
        passages=reply.passages,
        covered=reply.covered,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        searches=tuple(q for q in searches if q),
        blocks=reply.blocks,
        terms=reply.terms,
    )


CHECK_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                },
                "required": ["id", "verdict"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def check_prompt(claims: Sequence[Claim]) -> str:
    """Return the user message of a check: each claim, its quotes, and their context."""
    blocks = []
    for claim in claims:
        quoted = "\n".join(f'- "{q}"' for q in claim.quotes) or "- (none)"
        block = f"[{claim.id}] {claim.text}\nQuoted sentences:\n{quoted}"
        around = [c for c in claim.context if c.strip()]
        if around:
            shown = "\n---\n".join(around)
            block += f"\nThe sources around the quotes:\n<<<\n{shown}\n>>>"
        blocks.append(block)
    return "\n\n".join(blocks)


def parse_verdicts(
    text: str,
    backend: str,
    claims: Sequence[Claim],
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> Verdicts:
    """Turn the JSON of a check into one verdict for each claim, in order.

    A claim the model gave no verdict, or a verdict outside ``VERDICTS``,
    counts as unsupported: a check that says nothing confirms nothing.
    Raise ``LanguageModelError`` naming *backend* when the text is not the
    JSON the schema asks for.
    """
    try:
        found = {str(v["id"]): str(v["verdict"]) for v in json.loads(text)["verdicts"]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LanguageModelError(
            f"{backend} did not reply with the verdicts of a check: {exc}"
        ) from exc
    verdicts = tuple(
        found[c.id] if found.get(c.id) in VERDICTS else UNSUPPORTED for c in claims
    )
    return Verdicts(verdicts, input_tokens, output_tokens)
