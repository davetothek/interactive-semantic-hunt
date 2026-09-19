"""Ollama adapter for the Embedder protocol.

Call the local Ollama daemon over HTTP with the standard library. The
daemon holds the model resident, so no process pays a model load, and
the adapter needs no third-party package.
"""

import logging
import os
import time
from collections.abc import Sequence

from ish.adapters.embedder.prefixes import PrefixingEmbedder

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"

# Send this many texts per request. The daemon serves one embedding
# request at a time, so this is also how long a search waits when an
# index run is going: a query queues behind the request in flight and
# nothing else. Measured with nomic-embed-text over 64 definitions, the
# whole run costs the same at every size while the wait tracks the
# batch: 97 s at 64, 23 s at 16, 12 s at 8. The vectors are unchanged,
# bit for bit, so a smaller batch buys the wait for nothing.
DEFAULT_BATCH_SIZE = 8
# A batch of large definitions can take minutes on a busy daemon.
TIMEOUT_SECONDS = 600
# What somebody watching a picker will wait. A query still queues behind
# the request an index run has in flight, so it must wait; a wait of
# minutes is a failure to report rather than an answer worth serving.
QUERY_TIMEOUT_SECONDS = 60

# How many times to send a batch the daemon did not answer. A dead socket
# held one index run for 10 hours while the daemon itself stayed healthy,
# and embedding is incremental per file, so another attempt costs one
# batch rather than the run.
RETRY_ATTEMPTS = 3
# Wait this long before the second attempt, then double it. A daemon that
# is loading a model, or restarting, needs time rather than another
# request.
RETRY_BACKOFF_SECONDS = 1.0


class _Transient(RuntimeError):
    """A failure worth another attempt.

    A timeout and an unreachable daemon can both pass. A refused request
    cannot, so it is an ordinary error and stops the run.
    """


def _normalize_host(host: str) -> str:
    """Accept a bare ``host:port`` as well as a full URL."""
    host = host.rstrip("/")
    if not host.startswith(("http://", "https://")):
        return f"http://{host}"
    return host


class OllamaEmbedder(PrefixingEmbedder):
    """Generate embeddings through a running Ollama daemon.

    Read ``OLLAMA_HOST`` when no host is given, matching the Ollama
    command-line tools.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        host: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        super().__init__(model_name)
        chosen = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        self.host = _normalize_host(chosen)
        self._batch_size = max(1, batch_size)

    @classmethod
    def from_option(cls, model: str) -> "OllamaEmbedder":
        """Build the backend the ``model`` option names. Empty means the default."""
        return cls(model) if model else cls()

    def _embed(
        self,
        texts: Sequence[str],
        timeout: float = TIMEOUT_SECONDS,
        attempts: int = RETRY_ATTEMPTS,
    ) -> Sequence[Sequence[float]]:
        """Encode texts into vectors, one batch of requests at a time."""
        items = list(texts)
        vectors: list[Sequence[float]] = []
        for start in range(0, len(items), self._batch_size):
            batch = items[start : start + self._batch_size]
            vectors.extend(self._sent(batch, timeout, attempts))
        return vectors

    def _embed_interactive(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode a query, waiting only as long as a person will.

        Send it once. Somebody is watching, and a second wait of a minute
        is worse than a message that says what went wrong.
        """
        return self._embed(texts, timeout=QUERY_TIMEOUT_SECONDS, attempts=1)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sent(
        self, batch: list[str], timeout: float, attempts: int
    ) -> list[Sequence[float]]:
        """Send one batch, and send it again when the daemon does not answer.

        Make the last attempt outside the loop, so its failure reaches
        the caller as it is.
        """
        wait = RETRY_BACKOFF_SECONDS
        for attempt in range(1, attempts):
            try:
                return self._embed_batch(batch, timeout)
            except _Transient as exc:
                log.warning(
                    "%s Sending it again in %.0f s. Attempt %d of %d.",
                    exc,
                    wait,
                    attempt + 1,
                    attempts,
                )
                time.sleep(wait)
                wait *= 2
        return self._embed_batch(batch, timeout)

    def _embed_batch(self, batch: list[str], timeout: float) -> list[Sequence[float]]:
        """Send one batch and return its vectors."""
        # The HTTP client costs 20 ms to import, which a process that
        # never embeds should not pay.
        import json
        import urllib.error
        import urllib.request

        payload = json.dumps({"model": self.model_name, "input": batch}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read())
        except TimeoutError as exc:
            raise _Transient(
                f"Ollama at {self.host} did not answer within {timeout:g} s. "
                f"It serves one embedding request at a time, so a query waits "
                f"for the batch an index run has in flight."
            ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            if exc.code == 404:
                hint = f"Pull it with 'ollama pull {self.model_name}'."
            else:
                hint = (
                    f"Confirm that {self.model_name!r} is an embedding model, "
                    f"not a generation model."
                )
            raise RuntimeError(
                f"Ollama refused the request ({exc.code}): {detail}. {hint}"
            ) from exc
        except urllib.error.URLError as exc:
            raise _Transient(
                f"Cannot reach Ollama at {self.host}: {exc.reason}. "
                f"Start it with 'ollama serve', or install another backend "
                f"and select it, as in "
                f"'pip install interactive-semantic-hunt[llama]' "
                f"then '--embedder llama.cpp'."
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama at {self.host} returned a reply that is not JSON."
            ) from exc

        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(batch):
            got = len(embeddings) if isinstance(embeddings, list) else 0
            raise RuntimeError(
                f"Ollama returned {got} vectors for {len(batch)} texts. "
                f"Confirm that {self.model_name!r} is an embedding model."
            )
        return embeddings
