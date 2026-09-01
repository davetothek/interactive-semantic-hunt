"""Search several indexes as one.

A tree is often indexed in parts, because a submodule or a subdirectory
was searched on its own. Searching a directory above them should reach
everything below it without rebuilding a single index over the lot.

Only reading is shared. Every write goes to the primary index, the one
whose root is the directory the caller named. A search from a parent
must never rewrite, prune, or delete what belongs to a subtree.
"""

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path

from ish.application.ports.vector_store import FileStamp, VectorStore
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)


class FederatedVectorStore:
    """Read from many indexes, write to one."""

    def __init__(
        self, primary: VectorStore | None, others: Sequence[VectorStore]
    ) -> None:
        self._primary = primary
        self._others = list(others)
        log.debug(
            "Federating %d indexes%s",
            len(self._others) + (1 if primary else 0),
            " with no writable index" if primary is None else "",
        )

    @property
    def writable(self) -> bool:
        """Return True when this store may be refreshed."""
        return self._primary is not None

    def _all(self) -> list[VectorStore]:
        stores = list(self._others)
        if self._primary is not None:
            stores.insert(0, self._primary)
        return stores

    # ------------------------------------------------------------------
    # Writing, which only ever reaches the primary
    # ------------------------------------------------------------------

    def file_stamps(self) -> Mapping[Path, FileStamp]:
        """Return only the primary's files, so a refresh manages only those."""
        return self._primary.file_stamps() if self._primary else {}

    def missing_vectors(self, hashes: Collection[str]) -> set[str]:
        return self._primary.missing_vectors(hashes) if self._primary else set(hashes)

    def add_vectors(self, vectors: Mapping[str, Sequence[float]]) -> None:
        if self._primary is not None:
            self._primary.add_vectors(vectors)

    def set_file(
        self, path: Path, stamp: FileStamp, chunks: Sequence[tuple[Chunk, str]]
    ) -> None:
        if self._primary is not None:
            self._primary.set_file(path, stamp, chunks)

    def remove_files(self, paths: Collection[Path]) -> None:
        if self._primary is not None:
            self._primary.remove_files(paths)

    def clear(self) -> None:
        if self._primary is not None:
            self._primary.clear()

    # ------------------------------------------------------------------
    # Reading, which reaches every index
    # ------------------------------------------------------------------

    def chunks(self) -> Sequence[Chunk]:
        """Return every chunk from every index, ordered by path then line.

        Drop a repeat. Indexing a tree and one of its subdirectories
        stores the same chunk twice, and a listing must show it once.
        """
        seen: set[Chunk] = set()
        for store in self._all():
            seen.update(store.chunks())
        return sorted(seen, key=lambda c: (str(c.path), c.start_line))

    def search(
        self,
        query_vector: Sequence[float],
        query_text: str = "",
        limit: int = 5,
        keep: Callable[[Chunk], bool] | None = None,
    ) -> Sequence[tuple[Chunk, float]]:
        """Merge the best results from every index.

        Ask each index for a full page, then keep the best overall. Every
        index holds vectors from the same model, so the scores compare.
        """
        best: dict[Chunk, float] = {}
        for store in self._all():
            for chunk, score in store.search(query_vector, query_text, limit, keep):
                # A tree and its subdirectory may both hold this chunk.
                # Keep it once, at its best score.
                if score > best.get(chunk, float("-inf")):
                    best[chunk] = score

        ranked = sorted(best.items(), key=lambda pair: pair[1], reverse=True)
        return ranked[:limit]

    def close(self) -> None:
        """Release every index."""
        for store in self._all():
            store.close()
