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
from ish.adapters.embedder.turn import Turn, default_turn

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"

# Send this many texts per request. The daemon serves one embedding
# request at a time, so this is also how long a search waits when it
# arrives during an index run: a query queues behind the request in
# flight and nothing else, because the run reads the marker the query
# leaves and holds its next request back. One text a request bounds
# that wait to one text. Each text is embedded on its own inside a
# request, so the vectors are the same bit for bit at any size. The
# run pays for it in throughput on small chunks: 64 chunks of about
# 250 tokens embed in 9.1 s one at a time against 7.5 s in requests of
# 8, and 16 chunks of about 1000 tokens in 9.3 s either way, median of
# 5 on a CPU daemon.
DEFAULT_BATCH_SIZE = 1
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


def _is_loopback(host: str) -> bool:
    """Return True when the host names this machine."""
    # Only the error path calls this, so pay the imports here.
    import ipaddress
    import urllib.parse

    name = urllib.parse.urlsplit(host).hostname or ""
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


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
        context_tokens: int | None = None,
        turn: Turn | None = None,
    ) -> None:
        super().__init__(model_name)
        chosen = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        self.host = _normalize_host(chosen)
        self._batch_size = max(1, batch_size)
        # The marker a query leaves, which holds a document request back.
        # Every process of the user shares one, because they share the
        # one slot of the daemon.
        self.turn = turn if turn is not None else default_turn()
        # The window the daemon reads of each text, or None for its own
        # default. The daemon launches an embedding model with 2048 and
        # drops what lies past the window with no signal.
        self.context_tokens = context_tokens

    @classmethod
    def from_option(
        cls, model: str, context_tokens: int | None = None
    ) -> "OllamaEmbedder":
        """Build the backend the ``model`` option names. Empty means the default."""
        return cls(model or DEFAULT_MODEL, context_tokens=context_tokens)

    def _embed(
        self,
        texts: Sequence[str],
        timeout: float = TIMEOUT_SECONDS,
        attempts: int = RETRY_ATTEMPTS,
        *,
        take_turns: bool = True,
    ) -> Sequence[Sequence[float]]:
        """Encode texts into vectors, one batch of requests at a time.

        Before each request, wait while somebody embeds a query, so a
        person typing in a picker gets the slot of the daemon first.
        """
        items = list(texts)
        vectors: list[Sequence[float]] = []
        for start in range(0, len(items), self._batch_size):
            if take_turns:
                waited = self.turn.wait()
                if waited:
                    log.debug("Waited %.1f s for a search to go first.", waited)
            batch = items[start : start + self._batch_size]
            vectors.extend(self._sent(batch, timeout, attempts))
        return vectors

    def _embed_interactive(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode a query, waiting only as long as a person will.

        Leave the marker first, so an index run holds its next request
        back. Then send once. Somebody is watching, and a second wait of
        a minute is worse than a message that says what went wrong.
        """
        self.turn.mark()
        return self._embed(
            texts, timeout=QUERY_TIMEOUT_SECONDS, attempts=1, take_turns=False
        )

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

    def _unreachable_hint(self) -> str:
        """Say how to reach the daemon, for a local host or a remote one."""
        if not _is_loopback(self.host):
            return (
                "OLLAMA_HOST names that machine. Make sure the daemon runs "
                "there and accepts a remote connection. To embed on this "
                "machine instead, unset OLLAMA_HOST."
            )
        return (
            "Start it with 'ollama serve', or install another backend and "
            "select it, as in 'pip install interactive-semantic-hunt[llama]' "
            "then '--embedder llama.cpp'."
        )

    def _embed_batch(self, batch: list[str], timeout: float) -> list[Sequence[float]]:
        """Send one batch and return its vectors."""
        # The HTTP client costs 20 ms to import, which a process that
        # never embeds should not pay.
        import json
        import urllib.error
        import urllib.request

        body: dict[str, object] = {"model": self.model_name, "input": batch}
        if self.context_tokens is not None:
            body["options"] = {"num_ctx": self.context_tokens}
        payload = json.dumps(body).encode("utf-8")
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
                f"{self._unreachable_hint()}"
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
