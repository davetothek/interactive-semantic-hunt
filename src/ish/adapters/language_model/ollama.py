"""Answer from sources with a generation model that a local Ollama daemon serves.

Ollama has no citation feature, so the model replies in the JSON that
``_grounded`` fixes, with ``format`` set to its schema.

The text of a document stays on this machine. ``OLLAMA_HOST`` may name a
daemon elsewhere, which suits embedding vectors but not sending text, so
this backend refuses any host that is not a loopback address.
"""

import os
from collections.abc import Sequence

from ish.adapters.embedder.ollama import DEFAULT_HOST, _is_loopback, _normalize_host
from ish.adapters.language_model import _grounded
from ish.application.ports.language_model import LanguageModelError, Reply, Source

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
        chosen = _normalize_host(host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST)
        if not _is_loopback(chosen):
            raise LanguageModelError(
                f"The ollama language model only sends text to this machine, "
                f"and {chosen} is another. Point OLLAMA_HOST at a local daemon."
            )
        self.model_name = model
        self.host = chosen
        self._timeout = timeout

    def answer(self, question: str, sources: Sequence[Source]) -> Reply:
        """Answer *question* from *sources* in the JSON the schema fixes."""
        body = self._post(
            {
                "model": self.model_name,
                "stream": False,
                "format": _grounded.SCHEMA,
                "options": {"num_ctx": CONTEXT_TOKENS},
                "messages": [
                    {"role": "system", "content": _grounded.SYSTEM_PROMPT},
                    {"role": "user", "content": _grounded.prompt(question, sources)},
                ],
            }
        )
        message = body.get("message") or {}
        return _grounded.parse(
            str(message.get("content") or ""),
            f"Ollama model {self.model_name}",
            input_tokens=int(body.get("prompt_eval_count") or 0),
            output_tokens=int(body.get("eval_count") or 0),
        )

    def _post(self, payload: dict[str, object]) -> dict:
        """Send one chat request and return the decoded reply."""
        # The HTTP client costs 20 ms to import, which a search never pays.
        import json
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read())
        except TimeoutError as exc:
            raise LanguageModelError(
                f"Ollama at {self.host} did not answer within {self._timeout:g} s."
            ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            hint = (
                f" Pull it with 'ollama pull {self.model_name}'."
                if exc.code == 404
                else ""
            )
            raise LanguageModelError(
                f"Ollama refused the request ({exc.code}): {detail}.{hint}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LanguageModelError(
                f"Cannot reach Ollama at {self.host}: {exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LanguageModelError(
                f"Ollama at {self.host} returned a reply that is not JSON."
            ) from exc
