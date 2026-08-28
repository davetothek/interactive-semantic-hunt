"""Implement the search use case — discover, parse, embed, store, query."""

import logging
from collections.abc import Sequence
from pathlib import Path

from ish.application.ports.embedder import Embedder
from ish.application.ports.parser import Parser
from ish.application.ports.vector_store import VectorStore
from ish.application.scan import Scan
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
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        # Reuse the scan orchestration for the first step
        self._scanner = Scan(parsers=parsers)

    def build_index(self, root: Path) -> Sequence[Chunk] | None:
        """Scan the directory and embed all chunks into the vector store.

        Returns the chunks if indexed, None otherwise.
        """
        log.info("Scanning for source files to build search index...")
        chunks = self._scanner.run(root)

        chunks = list(chunks)
        if not chunks:
            log.warning("No chunks found to index.")
            return None

        log.info("Generating embeddings for %d chunks...", len(chunks))
        # Format the text with the symbol name to give the embedding model more context
        texts_to_embed = [f"{c.kind} {c.symbol}:\n{c.text}" for c in chunks]
        embeddings = self._embedder.embed(texts_to_embed)

        log.info("Storing embeddings in vector store...")
        self._vector_store.add(chunks, embeddings)
        log.info("Index built successfully.")
        return chunks

    def search(self, query: str, limit: int = 5) -> Sequence[tuple[Chunk, float]]:
        """Query the vector store with the semantic query."""
        log.info("Embedding search query...")
        query_embeddings = self._embedder.embed([query])
        if not query_embeddings:
            return []

        log.info("Searching vector store...")
        return self._vector_store.search(query_embeddings[0], limit=limit)

    def run(
        self, root: Path, query: str, limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the best matching chunks for a semantic query."""
        if self.build_index(root) is None:
            return []
        return self.search(query, limit)
