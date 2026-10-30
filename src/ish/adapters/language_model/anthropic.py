"""Answer from sources with the Anthropic Messages API and its native citations.

Each source goes to the model as one plain-text document with citations
on. The model returns text blocks, and each citation in a block names
the document it quotes by index. That index maps back to a source id.

The endpoint is fixed. Text of a document goes to the Anthropic API and
nowhere else, so there is no option that points this backend at another
host. The key comes from ``ANTHROPIC_API_KEY`` and never from a config
file, which may be shared or committed.
"""

import os
from collections.abc import Sequence

from ish.application.ports.language_model import (
    LanguageModelError,
    Passage,
    Reply,
    Source,
)

ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
KEY_VARIABLE = "ANTHROPIC_API_KEY"

# A long answer with its thinking fits well inside this. A reply cut at
# the limit is an error, not an answer.
MAX_TOKENS = 16_000
TIMEOUT_SECONDS = 300

# The reply the model gives when the documents do not answer the
# question. Citations cannot be combined with structured output, so a
# fixed word carries the flag.
NOT_COVERED = "NOT_COVERED"

SYSTEM_PROMPT = (
    "You answer questions about a set of internal documents. Use only the "
    "documents in the message. Cite the document that supports each claim. "
    "Do not use knowledge from outside the documents, and do not guess. "
    "If the documents do not answer the question, reply with exactly "
    f"{NOT_COVERED} and nothing else."
)


class AnthropicLanguageModel:
    """Write answers with a Claude model through the Messages API."""

    def __init__(self, model: str = "", *, timeout: float = TIMEOUT_SECONDS) -> None:
        """Use *model*, a Claude model id such as ``claude-sonnet-5-5``.

        Require the model. A default chosen here would decide the cost of
        every answer without the operator saying so.
        """
        if not model:
            raise LanguageModelError(
                "Set answer_model to a Claude model id for the anthropic backend."
            )
        self.model_name = model
        self._timeout = timeout

    def answer(self, question: str, sources: Sequence[Source]) -> Reply:
        """Answer *question* from *sources*, with a citation per claim."""
        body = self._post(_request(self.model_name, question, sources))
        stop = body.get("stop_reason")
        if stop == "refusal":
            raise LanguageModelError(f"{self.model_name} declined to answer.")
        if stop == "max_tokens":
            raise LanguageModelError(
                f"{self.model_name} reached {MAX_TOKENS} tokens before it finished."
            )
        usage = body.get("usage") or {}
        passages = _passages(body.get("content") or [], sources)
        text = "".join(p.text for p in passages).strip()
        return Reply(
            passages=passages,
            covered=bool(text) and not text.startswith(NOT_COVERED),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )

    def _post(self, payload: dict[str, object]) -> dict:
        """Send one request and return the decoded reply."""
        # The HTTP client costs 20 ms to import, which a search never pays.
        import json
        import urllib.error
        import urllib.request

        key = os.environ.get(KEY_VARIABLE, "")
        if not key:
            raise LanguageModelError(
                f"Set {KEY_VARIABLE} to use the anthropic backend."
            )
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": key,
                "anthropic-version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read())
        except TimeoutError as exc:
            raise LanguageModelError(
                f"The Anthropic API did not answer within {self._timeout:g} s."
            ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise LanguageModelError(
                f"The Anthropic API refused the request ({exc.code}): {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LanguageModelError(
                f"Cannot reach the Anthropic API: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LanguageModelError(
                "The Anthropic API returned a reply that is not JSON."
            ) from exc


def _request(model: str, question: str, sources: Sequence[Source]) -> dict[str, object]:
    documents: list[dict[str, object]] = [
        {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": s.text},
            "title": s.title,
            "citations": {"enabled": True},
        }
        for s in sources
    ]
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [*documents, {"type": "text", "text": question}],
            }
        ],
    }


def _passages(content: list[dict], sources: Sequence[Source]) -> tuple[Passage, ...]:
    """Turn the text blocks of a reply into passages that cite source ids.

    Skip blocks that are not text, such as thinking. Map each citation by
    its document index, and skip an index outside the sources sent.
    """
    passages = []
    for block in content:
        if block.get("type") != "text":
            continue
        ids: list[str] = []
        for citation in block.get("citations") or []:
            index = citation.get("document_index")
            if isinstance(index, int) and 0 <= index < len(sources):
                source_id = sources[index].id
                if source_id not in ids:
                    ids.append(source_id)
        passages.append(
            Passage(text=str(block.get("text") or ""), source_ids=tuple(ids))
        )
    return tuple(passages)
