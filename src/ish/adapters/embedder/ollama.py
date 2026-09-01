"""Ollama adapter for the Embedder protocol.

Call the local Ollama daemon over HTTP with the standard library. The
daemon holds the model resident, so no process pays a model load, and
the adapter needs no third-party package.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Sequence

log = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"

# Send this many texts per request. One request for a whole repository
# would hold the daemon for minutes and risk the timeout.
DEFAULT_BATCH_SIZE = 64
TIMEOUT_SECONDS = 120


def _normalize_host(host: str) -> str:
    """Accept a bare ``host:port`` as well as a full URL."""
    host = host.rstrip("/")
    if not host.startswith(("http://", "https://")):
        return f"http://{host}"
    return host


class OllamaEmbedder:
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
        self.model_name = model_name
        chosen = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        self.host = _normalize_host(chosen)
        self._batch_size = max(1, batch_size)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode texts into vectors, one batch of requests at a time."""
        if not texts:
            return []

        items = list(texts)
        vectors: list[Sequence[float]] = []
        for start in range(0, len(items), self._batch_size):
            vectors.extend(self._embed_batch(items[start : start + self._batch_size]))
        return vectors

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_batch(self, batch: list[str]) -> list[Sequence[float]]:
        """Send one batch and return its vectors."""
        payload = json.dumps({"model": self.model_name, "input": batch}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.host}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = json.loads(response.read())
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
            raise RuntimeError(
                f"Cannot reach Ollama at {self.host}: {exc.reason}. "
                f"Start it with 'ollama serve', or select another backend "
                f"with '--embedder llama.cpp'."
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
