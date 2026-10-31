"""Ask for an answer with citations from a model that has no citation feature.

The model reads the sources, each under its id, and replies in JSON that
a schema fixes: whether the sources cover the question, a short answer,
sections of prose, steps, or tables, and what the sources leave open.
Every passage and every table row names the ids it rests on. The use
case drops an id it did not send, so a model that invents a citation
cites nothing.
"""

import json
from collections.abc import Sequence

from ish.application.ports.language_model import (
    Block,
    LanguageModelError,
    Passage,
    Reply,
    Row,
    Selection,
    Source,
)

# Points of what the sources leave open that an answer keeps. The answer
# instruction asks for at most this many, and the parser keeps no more.
MAX_GAPS = 5

PASSAGE: dict[str, object] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "source_ids"],
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
                },
                "required": ["cells", "source_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["heading", "style", "passages", "columns", "rows"],
    "additionalProperties": False,
}

ANSWER_PROPERTIES: dict[str, object] = {
    "covered": {"type": "boolean"},
    "summary": PASSAGE,
    "sections": {"type": "array", "items": SECTION},
    "gaps": PASSAGES,
}

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": ANSWER_PROPERTIES,
    "required": ["covered", "summary", "sections", "gaps"],
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
    )


def _passage(data: dict) -> Passage:
    return Passage(
        text=str(data["text"]),
        source_ids=tuple(str(i) for i in data.get("source_ids") or ()),
    )


def _blocks(data: dict) -> tuple[Block, ...]:
    """Read the summary, the sections, and the gaps, and drop what is empty."""
    blocks: list[Block] = []
    summary = data.get("summary")
    if summary and str(summary.get("text") or "").strip():
        blocks.append(Block(kind="summary", passages=(_passage(summary),)))
    for section in data.get("sections") or ():
        style = str(section.get("style") or "prose")
        kind = style if style in ("prose", "steps", "table") else "prose"
        rows = tuple(
            Row(
                cells=tuple(str(c) for c in row["cells"]),
                source_ids=tuple(str(i) for i in row.get("source_ids") or ()),
            )
            for row in section.get("rows") or ()
        )
        passages = tuple(
            _passage(p) for p in section.get("passages") or () if str(p["text"]).strip()
        )
        if kind == "table" and not rows:
            kind = "prose"
        if passages or rows:
            blocks.append(
                Block(
                    kind=kind,
                    heading=str(section.get("heading") or "").strip(),
                    passages=passages,
                    columns=tuple(str(c) for c in section.get("columns") or ()),
                    rows=rows if kind == "table" else (),
                )
            )
    gaps = tuple(_passage(p) for p in data.get("gaps") or () if str(p["text"]).strip())[
        :MAX_GAPS
    ]
    if gaps:
        blocks.append(Block(kind="gaps", passages=gaps))
    return tuple(blocks)


def _flat(blocks: Sequence[Block]) -> list[Passage]:
    """Return every passage and every table row, as passages, in reading order."""
    flat: list[Passage] = []
    for block in blocks:
        flat.extend(block.passages)
        flat.extend(Passage(" | ".join(r.cells), r.source_ids) for r in block.rows)
    return flat


SELECT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["ids"],
    "additionalProperties": False,
}


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
    "required": ["covered", "summary", "sections", "gaps", "search"],
    "additionalProperties": False,
}


def step_prompt(
    question: str, sources: Sequence[Source], searched: Sequence[str], turns_left: int
) -> str:
    """Return the user message of one turn: the sources, what ran, what is left."""
    ran = "\n".join(f"- {q}" for q in searched) or "- none"
    return "\n\n".join(
        [
            prompt(question, sources),
            f"Searches already run:\n{ran}",
            f"Turns left for searches: {turns_left}",
        ]
    )


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
    )
