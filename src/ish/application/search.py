"""Implement the search use case — refresh the index, then query it."""

import logging
import math
from collections.abc import Sequence
from pathlib import Path

from ish.application.index import Index, window_digests
from ish.application.ports.embedder import Embedder
from ish.application.ports.vector_store import VectorReader
from ish.application.progress import ProgressCallback
from ish.application.ranking import ResultFilter
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)

# What a warm-up embeds. The vector is thrown away. The request is
# what matters: it makes the daemon load the model.
WARM_QUERY = "warm the model"


class Search:
    """Orchestrate semantic search over a directory tree."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        reader: VectorReader,
        index: Index | None = None,
        hybrid: bool = True,
        keep: ResultFilter = None,
    ) -> None:
        """Search what *reader* holds, refreshed by *index* when there is one.

        A search over several indexes at once has no index of its own to
        refresh: choosing one to write to would be wrong, so it reads
        what is stored.
        """
        self._embedder = embedder
        self._reader = reader
        self._index = index
        self._hybrid = hybrid
        self._keep = keep

    def close(self) -> None:
        """Release the store."""
        self._reader.close()

    def build_index(
        self, root: Path, on_progress: ProgressCallback | None = None
    ) -> int:
        """Bring the index in step with *root*. Return how many chunks it holds.

        Count rather than list, so a query does not read every stored
        chunk on its way to the few it will return.
        """
        if self._index is None:
            log.info("Searching stored indexes without refreshing")
        else:
            stats = self._index.refresh(root, on_progress)
            log.info(
                "Index ready: %d files, %d chunks written, %d vectors embedded",
                stats.files_seen,
                stats.chunks_indexed,
                stats.vectors_embedded,
            )
        held = self._reader.count()
        if not held:
            log.warning("No chunks found to index.")
        return held

    def held(self) -> int:
        """Return how many chunks the reader holds now, without refreshing."""
        return self._reader.count()

    def warm(self) -> None:
        """Load the model behind the backend before anyone waits on it.

        A daemon unloads an idle model and loads it again on the next
        request, which costs seconds. Pay that here, off the keystroke
        path, so the first query answers as fast as the second. Report
        a failure at INFO and leave it: the first real query meets the
        same failure and names it to the person waiting.
        """
        try:
            self._embedder.embed_query(WARM_QUERY)
        except Exception as exc:  # noqa: BLE001 - the first query reports it
            log.info("Cannot warm the backend: %s", exc)

    def indexed_paths(self) -> Sequence[Path]:
        """Return every file the store has read, chunks or none."""
        return self._reader.indexed_paths()

    def all_chunks(self, keep: ResultFilter = None) -> list[Chunk]:
        """Return the chunks the store holds, for a plain listing.

        Apply the same result filter a search would, so the listing and
        the search agree on what is in view.
        """
        chosen = keep or self._keep
        chunks = self._reader.chunks()
        if chosen is None:
            return list(chunks)
        return [chunk for chunk in chunks if chosen(chunk)]

    def passage(self, chunk: Chunk, query: str) -> str | None:
        """Return the window of *chunk* nearest *query*, or None when it has none.

        *chunk* carries its text as the index parsed it. Score each window
        by the vector the index stored for it, so nothing is embedded but
        the query, which the embedder has already seen. A reader then sees
        the few lines that matched, not the first lines of the section.
        """
        pairs = window_digests(chunk)
        if not pairs or not query.strip():
            return None
        stored = self._reader.vectors([digest for _window, digest in pairs])
        if not stored:
            return None
        query_vector = self._embedder.embed_query(query)
        scored = [
            (_cosine(query_vector, stored[digest]), window)
            for window, digest in pairs
            if digest in stored
        ]
        return max(scored, key=lambda pair: pair[0])[1]

    def search(
        self,
        query: str,
        limit: int = 5,
        keep: ResultFilter = None,
        hybrid: bool | None = None,
    ) -> Sequence[Match]:
        """Query the vector store with the semantic query.

        Accept a filter for this call alone, so a long-lived interface
        can narrow one search without rebuilding anything.
        """
        log.info("Embedding search query...")
        query_vector = self._embedder.embed_query(query)
        if not query_vector:
            return []

        log.info("Searching vector store...")
        use_hybrid = self._hybrid if hybrid is None else hybrid
        return self._reader.search(
            query_vector,
            query if use_hybrid else "",
            limit=limit,
            keep=keep or self._keep,
        )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the cosine similarity of two vectors, and 0 for a zero vector."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
