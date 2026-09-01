"""Vector Store protocol definition.

Satisfied by adapters that hold chunks with their embeddings, answer
similarity searches, and track enough state to refresh an index without
re-embedding unchanged work.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ish.domain.chunk import Chunk


@dataclass(frozen=True, slots=True)
class FileStamp:
    """Identify a file version without reading it.

    Compare the stamp a scan observes with the stamp the store holds to
    decide whether a file needs parsing again.
    """

    mtime_ns: int
    size: int


@runtime_checkable
class VectorStore(Protocol):
    """Contract for storing and searching vector embeddings."""

    def file_stamps(self) -> Mapping[Path, FileStamp]:
        """Return the stamp held for every indexed file."""
        ...

    def missing_vectors(self, hashes: Collection[str]) -> set[str]:
        """Return the subset of *hashes* that has no stored vector.

        Let the caller embed only what is absent.
        """
        ...

    def add_vectors(self, vectors: Mapping[str, Sequence[float]]) -> None:
        """Store vectors by content hash.

        Key by content so a moved file or an unchanged definition keeps
        its vector.
        """
        ...

    def set_file(
        self, path: Path, stamp: FileStamp, chunks: Sequence[tuple[Chunk, str]]
    ) -> None:
        """Replace everything held for *path*.

        Accept each chunk with the content hash of its embedded text.
        """
        ...

    def remove_files(self, paths: Collection[Path]) -> None:
        """Drop everything held for *paths*, for files that no longer exist."""
        ...

    def chunks(self) -> Sequence[Chunk]:
        """Return every chunk the store holds, for a plain listing."""
        ...

    def search(
        self, query_vector: Sequence[float], limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the *limit* most similar chunks to the *query_vector*.

        Return (Chunk, similarity_score) tuples, sorted by score in
        descending order.
        """
        ...

    def clear(self) -> None:
        """Discard every indexed file, so the next refresh rebuilds."""
        ...

    def close(self) -> None:
        """Release any resource the store holds."""
        ...
