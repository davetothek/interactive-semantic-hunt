"""Implement the search use case — refresh the index, then query it."""

import logging
from collections.abc import Sequence
from pathlib import Path

from ish.application.index import Index
from ish.application.ports.embedder import Embedder
from ish.application.ports.vector_store import VectorReader
from ish.application.progress import ProgressCallback
from ish.application.ranking import ResultFilter
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)


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
