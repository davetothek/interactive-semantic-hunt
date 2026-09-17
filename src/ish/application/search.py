"""Implement the search use case — refresh the index, then query it."""

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from ish.application.index import Index
from ish.application.ports.embedder import Embedder
from ish.application.ports.vector_store import VectorStore
from ish.application.ranking import ResultFilter
from ish.application.scan import Scan
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)


class Search:
    """Orchestrate semantic search over a directory tree."""

    def __init__(
        self,
        *,
        scan: Scan,
        embedder: Embedder,
        vector_store: VectorStore,
        reindex: bool = False,
        hybrid: bool = True,
        keep: ResultFilter = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._reindex = reindex
        self._hybrid = hybrid
        self._keep = keep
        self._index = Index(
            scan=scan,
            embedder=embedder,
            vector_store=vector_store,
        )

    def close(self) -> None:
        """Release the store."""
        self._vector_store.close()

    def build_index(
        self, root: Path, on_progress: Callable[[str], None] | None = None
    ) -> Sequence[Chunk] | None:
        """Bring the index in step with *root*.

        Return the chunks the store now holds, or None when it holds none.
        """
        if not getattr(self._vector_store, "writable", True):
            # Reading several indexes at once. Refreshing would have to
            # choose one to write to, and any choice would be wrong.
            log.info("Searching stored indexes without refreshing")
            chunks = self.all_chunks()
            return chunks or None

        if self._reindex:
            log.info("Discarding the stored index for %s", root)
            self._vector_store.clear()
            self._reindex = False

        stats = self._index.refresh(root, on_progress)
        log.info(
            "Index ready: %d files, %d chunks written, %d vectors embedded",
            stats.files_seen,
            stats.chunks_indexed,
            stats.vectors_embedded,
        )
        chunks = self.all_chunks()
        if not chunks:
            log.warning("No chunks found to index.")
            return None
        return chunks

    def all_chunks(self, keep: ResultFilter = None) -> list[Chunk]:
        """Return the chunks the store holds, for a plain listing.

        Apply the same result filter a search would, so the listing and
        the search agree on what is in view.
        """
        chosen = keep or self._keep
        chunks = self._vector_store.chunks()
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
        return self._vector_store.search(
            query_vector,
            query if use_hybrid else "",
            limit=limit,
            keep=keep or self._keep,
        )

    def run(
        self,
        root: Path,
        query: str,
        limit: int = 5,
        keep: ResultFilter = None,
    ) -> Sequence[Match]:
        """Find the best matching chunks for a semantic query."""
        if self.build_index(root) is None:
            return []
        return self.search(query, limit, keep=keep)
