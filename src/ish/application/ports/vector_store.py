"""Vector Store protocol definition.

Satisfied by adapters that hold chunks with their embeddings, answer
similarity searches, and track enough state to refresh an index without
re-embedding unchanged work.
"""

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ish.domain.chunk import Chunk

# Rank constant from the Reciprocal Rank Fusion paper. Large enough that
# no single list can dominate on its top hit alone.
RRF_K = 60

# Weights for the fused rankings. The vector ranking is the stronger
# signal on this kind of corpus, so it carries three times the lexical
# weight. Measured on this repository over 20 queries: equal weights cost
# 10 points of top-1 accuracy, and 3 to 1 costs none.
SEMANTIC_WEIGHT = 3.0
LEXICAL_WEIGHT = 1.0

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def is_code_like(query: str) -> bool:
    """Return True when the query names something rather than describes it.

    Lexical matching earns its place only for a query that carries an
    identifier. Fusing it into a plain description costs accuracy,
    because the vector ranking is already the better signal there.
    """
    for token in _TOKEN.findall(query):
        if "_" in token:
            return True
        if len(token) > 2 and token.isupper():
            return True
        # Mixed case, such as IshApp or HTTPServer. An all-capital word is
        # already handled above, so a short one stays ordinary prose.
        if not token.isupper() and any(char.isupper() for char in token[1:]):
            return True
    return False


def split_identifier(name: str) -> str:
    """Split an identifier into the words it is built from.

    Turn ``PythonParser.parse`` into ``Python Parser parse`` so a lexical
    search matches a word inside a name, not only the whole name.
    """
    words: list[str] = []
    for part in _WORD.findall(name or ""):
        words.extend(_CAMEL.findall(part))
    return " ".join(words)


def fuse_rankings(
    rankings: Sequence[tuple[Sequence[Chunk], float]], limit: int
) -> list[Chunk]:
    """Merge weighted ranked lists with Reciprocal Rank Fusion.

    Score each chunk by ``weight / (RRF_K + rank)`` in every list it
    appears in. A chunk that both retrievers rank well beats one that
    only a single retriever loves, which is the point of running two.
    """
    scores: dict[Chunk, float] = {}
    for ranking, weight in rankings:
        for rank, chunk in enumerate(ranking, 1):
            scores[chunk] = scores.get(chunk, 0.0) + weight / (RRF_K + rank)

    ordered = sorted(scores, key=lambda chunk: -scores[chunk])
    return ordered[:limit]


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
        """Return every chunk the store holds, for a plain listing.

        A returned chunk may carry no text. A store records where a
        chunk is, and a caller that needs the source reads the file.
        """
        ...

    def search(
        self,
        query_vector: Sequence[float],
        query_text: str = "",
        limit: int = 5,
        keep: Callable[[Chunk], bool] | None = None,
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the *limit* best chunks for a query.

        Rank by vector similarity alone when *query_text* is empty.
        Otherwise also rank the text lexically and fuse the two orders,
        which recovers exact identifiers that a vector alone can miss.

        Apply *keep* before the limit, so a filtered search still
        returns a full page of results.

        Return (Chunk, similarity_score) tuples in rank order. The score
        stays the cosine similarity, so it means the same thing whether
        or not the lexical half ran.
        """
        ...

    def clear(self) -> None:
        """Discard every indexed file, so the next refresh rebuilds."""
        ...

    def close(self) -> None:
        """Release any resource the store holds."""
        ...
