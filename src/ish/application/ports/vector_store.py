"""Vector store protocol definitions.

Two contracts, because two use cases need different halves. A search
reads: it lists, counts, and ranks. A refresh also writes: it stamps
files, stores vectors, and prunes. A store that federates several
indexes can only read, so it satisfies the reader alone, and a search
over it needs no special case.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ish.application.ranking import ResultFilter
from ish.domain.chunk import Chunk
from ish.domain.match import Match


class StoreBusy(Exception):
    """Raise when another process holds the index and will not let go.

    A store waits a short time for a lock and then says so. A caller
    that waits without a limit cannot tell a slow index run from a
    dead one, and it reports nothing while it waits.
    """


@dataclass(frozen=True, slots=True)
class FileStamp:
    """Identify a file version without reading it.

    Compare the stamp a scan observes with the stamp the store holds to
    decide whether a file needs parsing again.
    """

    mtime_ns: int
    size: int


@runtime_checkable
class VectorReader(Protocol):
    """Contract for listing and searching stored chunks."""

    def chunks(self) -> Sequence[Chunk]:
        """Return every chunk the store holds, for a plain listing.

        A returned chunk may carry no text. A store records where a
        chunk is, and a caller that needs the source reads the file.
        """
        ...

    def count(self) -> int:
        """Return how many chunks the store holds, without building them."""
        ...

    def indexed_paths(self) -> Sequence[Path]:
        """Return every file the store has read, whether or not it yielded chunks.

        A file that yielded nothing is stamped so it is read once, and a
        status that counted only chunks could not say it was there.
        """
        ...

    def search(
        self,
        query_vector: Sequence[float],
        query_text: str = "",
        limit: int = 5,
        keep: ResultFilter = None,
    ) -> Sequence[Match]:
        """Find the *limit* best chunks for a query.

        Rank by vector similarity alone when *query_text* is empty.
        Otherwise also rank the text lexically and fuse the two orders,
        which recovers exact identifiers that a vector alone can miss.

        Apply *keep* before the limit, so a filtered search still
        returns a full page of results.

        Return matches in rank order. The score stays the cosine
        similarity, so it means the same thing whether or not the
        lexical half ran.
        """
        ...

    def close(self) -> None:
        """Release any resource the store holds."""
        ...


@runtime_checkable
class VectorStore(VectorReader, Protocol):
    """Contract for a store a refresh may also write to."""

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

    def clear(self) -> None:
        """Discard every indexed file, so the next refresh rebuilds."""
        ...

    def chunking(self) -> str:
        """Return the chunking stamp the files were read under, or empty.

        The stamp names how files were divided into chunks. A refresh
        that finds a different one reads every file again and reuses
        every vector, because a vector is keyed by content.
        """
        ...

    def set_chunking(self, stamp: str) -> None:
        """Record the chunking stamp the files are read under from now on."""
        ...
