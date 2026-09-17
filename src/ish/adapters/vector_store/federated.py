"""Search several indexes as one.

A tree is often indexed in parts, because a submodule or a subdirectory
was searched on its own. Searching a directory above them should reach
everything below it without rebuilding a single index over the lot.

Only reading is shared. A search from a parent must never rewrite,
prune, or delete what belongs to a subtree, so this reader offers no
way to write. A refresh goes to the one index whose tree was named,
and the composition root hands that index to the refresh directly.
"""

import logging
from collections.abc import Sequence

from ish.application.ports.vector_store import VectorReader
from ish.application.ranking import ResultFilter
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)


class FederatedReader:
    """Read from many indexes as if they were one."""

    def __init__(self, readers: Sequence[VectorReader]) -> None:
        self._readers = list(readers)
        log.debug("Federating %d indexes", len(self._readers))

    def chunks(self) -> Sequence[Chunk]:
        """Return every chunk from every index, ordered by path then line.

        Drop a repeat. Indexing a tree and one of its subdirectories
        stores the same chunk twice, and a listing must show it once.
        """
        seen: set[Chunk] = set()
        for reader in self._readers:
            seen.update(reader.chunks())
        return sorted(seen, key=lambda c: (str(c.path), c.start_line))

    def count(self) -> int:
        """Return how many distinct chunks the indexes hold together.

        Two indexes may hold one chunk, so the answer needs the chunks
        themselves once there is more than one index to ask.
        """
        if len(self._readers) == 1:
            return self._readers[0].count()
        return len(self.chunks())

    def search(
        self,
        query_vector: Sequence[float],
        query_text: str = "",
        limit: int = 5,
        keep: ResultFilter = None,
    ) -> Sequence[Match]:
        """Merge the best results from every index.

        Ask each index for a full page, then keep the best overall. Every
        index holds vectors from the same model, so the scores compare.
        """
        best: dict[Chunk, float] = {}
        for reader in self._readers:
            for chunk, score in reader.search(query_vector, query_text, limit, keep):
                # A tree and its subdirectory may both hold this chunk.
                # Keep it once, at its best score.
                if score > best.get(chunk, float("-inf")):
                    best[chunk] = score

        ranked = sorted(best.items(), key=lambda pair: pair[1], reverse=True)
        return [Match(chunk, score) for chunk, score in ranked[:limit]]

    def close(self) -> None:
        """Release every index."""
        for reader in self._readers:
            reader.close()
