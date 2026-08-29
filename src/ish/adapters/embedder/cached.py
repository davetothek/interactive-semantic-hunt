"""Caching wrapper for embedders to avoid regenerating embeddings."""

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

import diskcache

from ish.application.ports.embedder import Embedder


def _default_cache_dir() -> str:
    """Return the platform cache directory for ish.

    Honor ``XDG_CACHE_HOME`` and fall back to ``~/.cache``.
    """
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return str(Path(base) / "ish")


class CachedEmbedder(Embedder):
    """Wrap an Embedder and cache results to disk.

    Open the cache lazily on first use so construction stays cheap and
    touches no disk when the embedder is never called.
    """

    def __init__(
        self, embedder: Embedder, *, cache_dir: str | None = None
    ) -> None:
        self._embedder = embedder
        self._cache_dir = cache_dir or _default_cache_dir()
        self._cache: diskcache.Cache | None = None

    def close(self) -> None:
        """Close the underlying disk cache if it was opened."""
        if self._cache is not None:
            self._cache.close()
            self._cache = None

    def _get_cache(self) -> diskcache.Cache:
        """Return the disk cache, opening it on first access."""
        if self._cache is None:
            self._cache = diskcache.Cache(self._cache_dir)
        return self._cache

    def _hash_text(self, text: str) -> str:
        """Create a stable cache key for a text chunk.

        Include the adapter class and its model identity so caches stay
        isolated between backends and between models of one backend.
        """
        model = getattr(self._embedder, "model_name", "")
        key_data = f"{self._embedder.__class__.__name__}:{model}:{text}"
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Retrieve from cache, or generate and cache."""
        if not texts:
            return []

        cache = self._get_cache()
        results: list[Sequence[float]] = []
        to_generate_indices: list[int] = []
        to_generate_texts: list[str] = []

        # 1. Check cache for all texts
        for i, text in enumerate(texts):
            key = self._hash_text(text)
            cached_vector = cache.get(key)
            if cached_vector is not None:
                results.append(cached_vector)
            else:
                # Placeholder to keep index alignment
                results.append([])
                to_generate_indices.append(i)
                to_generate_texts.append(text)

        # 2. Generate missing embeddings in bulk
        if to_generate_texts:
            new_vectors = self._embedder.embed(to_generate_texts)
            for idx, text, vector in zip(
                to_generate_indices, to_generate_texts, new_vectors, strict=True
            ):
                results[idx] = vector
                key = self._hash_text(text)
                cache.set(key, vector)

        return results
