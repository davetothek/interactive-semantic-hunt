"""Link resolver protocol definition.

Satisfied by adapters that turn a chunk into a URL that opens its source
at the place the chunk sits. A reader of an answer follows the link to
check a claim, so the link has to reach the reader, not only the server.
"""

from typing import Protocol, runtime_checkable

from ish.domain.chunk import Chunk


@runtime_checkable
class LinkResolver(Protocol):
    """Contract for linking a chunk to its source."""

    def link(self, chunk: Chunk) -> str:
        """Return a URL that opens *chunk* at its location."""
        ...
