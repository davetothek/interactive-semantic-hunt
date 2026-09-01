"""Implement the search use case — refresh the index, then query it."""

import logging
from collections.abc import Sequence
from pathlib import Path

from ish.application.index import Index
from ish.application.ports.embedder import Embedder
from ish.application.ports.parser import Parser
from ish.application.ports.vector_store import VectorStore
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)


class Search:
    """Orchestrate semantic search over a directory tree."""

    def __init__(
        self,
        *,
        parsers: Sequence[Parser],
        embedder: Embedder,
        vector_store: VectorStore,
        ignored_dirs: Sequence[str] = (),
        reindex: bool = False,
        hybrid: bool = True,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._reindex = reindex
        self._hybrid = hybrid
        self._index = Index(
            parsers=parsers,
            embedder=embedder,
            vector_store=vector_store,
            ignored_dirs=ignored_dirs,
        )

    def close(self) -> None:
        """Release the store."""
        self._vector_store.close()

    def build_index(self, root: Path) -> Sequence[Chunk] | None:
        """Bring the index in step with *root*.

        Return the chunks the store now holds, or None when it holds none.
        """
        if self._reindex:
            log.info("Discarding the stored index for %s", root)
            self._vector_store.clear()
            self._reindex = False

        stats = self._index.refresh(root)
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

    def all_chunks(self) -> list[Chunk]:
        """Return every chunk the store holds, for a plain listing."""
        return list(self._vector_store.chunks())

    def search(self, query: str, limit: int = 5) -> Sequence[tuple[Chunk, float]]:
        """Query the vector store with the semantic query."""
        log.info("Embedding search query...")
        query_vector = self._embedder.embed_query(query)
        if not query_vector:
            return []

        log.info("Searching vector store...")
        return self._vector_store.search(
            query_vector, query if self._hybrid else "", limit=limit
        )

    def run(
        self, root: Path, query: str, limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the best matching chunks for a semantic query."""
        if self.build_index(root) is None:
            return []
        return self.search(query, limit)
