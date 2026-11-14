"""Answer from sources with a generation model that a local Ollama daemon serves.

Ollama has no citation feature, so the model replies in the JSON that
``_grounded`` fixes, with ``format`` set to its schema.

The text of a document stays on this machine. ``OLLAMA_HOST`` may name a
daemon elsewhere, which suits embedding vectors but not sending text, so
this backend refuses any host that is not a loopback address.
"""

from collections.abc import Sequence

from ish.adapters import _http
from ish.adapters._ollama import is_loopback, resolve_host
from ish.adapters.language_model import _grounded
from ish.adapters.language_model._prompts import Prompts
from ish.adapters.language_model._prompts import load as load_prompts
from ish.application.ports.language_model import (
    LanguageModelError,
    OnBlock,
    Query,
    Reply,
    Selection,
    Source,
)

# The window the daemon gives the model. Ollama starts a model with a
# window of a few thousand tokens and drops what does not fit without a
# signal, so an answer would read only the first sources. An answer may
# send 60,000 characters, about 15,000 tokens, plus the prompt and the
# reply.
CONTEXT_TOKENS = 20_480

# A generation model on a CPU writes slowly. An answer of a few hundred
# tokens can take minutes.
TIMEOUT_SECONDS = 600


class OllamaLanguageModel:
    """Write answers with a generation model on the local Ollama daemon."""

    def __init__(
        self,
        model: str = "",
        *,
        host: str | None = None,
        prompts: Prompts | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        """Use *model*, such as ``qwen3:8b``, on the daemon at *host*.

        Read ``OLLAMA_HOST`` when no host is given. Require the model, and
        refuse a host that is not this machine.
        """
        if not model:
            raise LanguageModelError(
                "Set answer_model to an Ollama generation model for the ollama backend."
            )
        chosen = resolve_host(host)
        if not is_loopback(chosen):
            raise LanguageModelError(
                f"The ollama language model only sends text to this machine, "
                f"and {chosen} is another. Point OLLAMA_HOST at a local daemon."
            )
        self.model_name = model
        self._prompts = prompts or load_prompts()
        self.host = chosen
        self._timeout = timeout

    def answer(
        self,
        question: str,
        sources: Sequence[Source],
        on_block: OnBlock | None = None,
    ) -> Reply:
        """Answer *question* from *sources* in the JSON the schema fixes.

        Stream nothing: *on_block* gets no block, and the reply holds them all.
        """
        text, read, wrote = self._chat(
            self._prompts.answer,
            _grounded.SCHEMA,
            _grounded.prompt(question, sources),
        )
        return _grounded.parse(text, f"Ollama model {self.model_name}", read, wrote)

    def step(
        self,
        question: str,
        sources: Sequence[Source],
        searched: Sequence[str],
        turns_left: int,
        on_block: OnBlock | None = None,
    ) -> Reply:
        """Answer, or ask for searches, in the JSON the schema fixes."""
        text, read, wrote = self._chat(
            self._prompts.step,
            _grounded.STEP_SCHEMA,
            _grounded.step_prompt(question, sources, searched, turns_left),
        )
        return _grounded.parse_step(
            text, f"Ollama model {self.model_name}", read, wrote
        )

    def select(
        self, question: str, candidates: Sequence[Source], limit: int
    ) -> Selection:
        """Choose at most *limit* candidates, in the JSON the schema fixes."""
        text, read, wrote = self._chat(
            self._prompts.select,
            _grounded.SELECT_SCHEMA,
            _grounded.select_prompt(question, candidates, limit),
        )
        return _grounded.parse_ids(
            text, f"Ollama model {self.model_name}", limit, read, wrote
        )

    def query(self, question: str, history: str) -> Query:
        """Write one search query, in the JSON the schema fixes."""
        text, read, wrote = self._chat(
            self._prompts.query,
            _grounded.QUERY_SCHEMA,
            _grounded.query_prompt(question, history),
        )
        return _grounded.parse_query(
            text, f"Ollama model {self.model_name}", read, wrote
        )

    def _chat(
        self, system: str, schema: dict[str, object], prompt: str
    ) -> tuple[str, int, int]:
        """Send one chat turn, and return its text and token counts."""
        body = self._post(
            {
                "model": self.model_name,
                "stream": False,
                "format": schema,
                "options": {"num_ctx": CONTEXT_TOKENS},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }
        )
        message = body.get("message") or {}
        return (
            str(message.get("content") or ""),
            int(body.get("prompt_eval_count") or 0),
            int(body.get("eval_count") or 0),
        )

    def _post(self, payload: dict[str, object]) -> dict:
        """Send one chat request and return the decoded reply."""
        try:
            return _http.post_json(
                f"{self.host}/api/chat", payload, timeout=self._timeout
            )
        except _http.Timeout as exc:
            raise LanguageModelError(
                f"Ollama at {self.host} did not answer within {self._timeout:g} s."
            ) from exc
        except _http.Refused as exc:
            hint = (
                f" Pull it with 'ollama pull {self.model_name}'."
                if exc.code == 404
                else ""
            )
            raise LanguageModelError(
                f"Ollama refused the request ({exc.code}): {exc.detail}.{hint}"
            ) from exc
        except _http.Unreachable as exc:
            raise LanguageModelError(
                f"Cannot reach Ollama at {self.host}: {exc.reason}"
            ) from exc
        except _http.NotJson as exc:
            raise LanguageModelError(
                f"Ollama at {self.host} returned a reply that is not JSON."
            ) from exc
