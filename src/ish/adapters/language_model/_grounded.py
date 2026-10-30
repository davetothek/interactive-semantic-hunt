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
    Source,
)

SYSTEM_PROMPT = (
    "You answer questions about a set of internal documents. Use only the "
    "sources in the message. Each source starts with its id in brackets. "
    "Reply in JSON. Split the answer into passages, and list for each "
    "passage the ids of the sources that support it. Do not use knowledge "
    "from outside the sources, and do not guess. If the sources do not "
    "answer the question, set covered to false and give no passages."
)

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "covered": {"type": "boolean"},
        "passages": {
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
        },
    },
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
