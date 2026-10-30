"""Ask for an answer with citations from a model that has no citation feature.

The model reads the sources, each under its id, and replies in JSON that
a schema fixes: whether the sources cover the question, and the answer
as passages that each name the ids they rest on. The use case drops an
id it did not send, so a model that invents a citation cites nothing.
"""

import json
from collections.abc import Sequence

from ish.application.ports.language_model import (
    LanguageModelError,
    Passage,
    Reply,
    Selection,
    Source,
)

SYSTEM_PROMPT = (
    "You answer questions about a set of internal documents. Use only the "
    "sources in the message. Each source starts with its id, a number in "
    "brackets such as [1], followed by its title. Reply in JSON. Split the "
    "answer into passages, and list for each passage the ids of the sources "
    'that support it, as the bare numbers such as "1", never the titles. '
    "Write short paragraphs of one topic each, and end the text of the last "
    "passage of a paragraph with two newline characters. "
    "Do not use knowledge "
    "from outside the sources, and do not guess. If the sources do not "
    "answer the question, set covered to false and give no passages."
)

PASSAGES: dict[str, object] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "source_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text", "source_ids"],
        "additionalProperties": False,
    },
}

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"covered": {"type": "boolean"}, "passages": PASSAGES},
    "required": ["covered", "passages"],
    "additionalProperties": False,
}


def prompt(question: str, sources: Sequence[Source]) -> str:
    """Return the user message: every source under its id, then the question."""
    blocks = [f"[{s.id}] {s.title}\n{s.text}" for s in sources]
    return "\n\n".join([*blocks, f"Question: {question}"])


def parse(
    text: str, backend: str, input_tokens: int = 0, output_tokens: int = 0
) -> Reply:
    """Turn the JSON a model wrote into a reply.

    Raise ``LanguageModelError`` naming *backend* when the text is not the
    JSON the schema asks for.
    """
    try:
        data = json.loads(text)
        covered = data["covered"]
        passages = tuple(
            Passage(
                text=str(p["text"]),
                source_ids=tuple(str(i) for i in p.get("source_ids") or ()),
            )
            for p in data["passages"]
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LanguageModelError(
            f"{backend} did not reply in the JSON the answer needs: {exc}"
        ) from exc
    if not isinstance(covered, bool):
        raise LanguageModelError(f"{backend} replied with covered={covered!r}.")
    return Reply(
        passages=passages,
        covered=covered and bool(passages),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


SELECT_PROMPT = (
    "You choose the sources worth reading to answer a question. Each "
    "candidate starts with its id, a number in brackets such as [1], then "
    "its title and the start of its text. Reply in JSON with the ids of the "
    "candidates most likely to hold the answer, best first, as the bare "
    'numbers such as "1". Prefer a source that states a rule or a design '
    "over one that only exercises it, such as a test case."
)

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


STEP_PROMPT = (
    SYSTEM_PROMPT
    + " You may ask for more searches before you answer. When the sources "
    "fall short, or name a term, an identifier, or a document worth finding, "
    "put up to three new search queries in search and give no passages. "
    "Write a query in new words: the search matches by meaning. When the "
    "sources are enough, or no turns are left, answer and leave search empty."
)

STEP_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "covered": {"type": "boolean"},
        "passages": PASSAGES,
        "search": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["covered", "passages", "search"],
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
    )
