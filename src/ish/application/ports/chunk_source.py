"""Chunk source protocol definition.

Satisfied by adapters that get ranked chunks from an index this machine
does not hold. A session that answers from such a source reads the text
of each chunk from the source as well, because it has no file of the tree.

The source searches, and nothing more. It runs no language model, so the
machine that asks decides which model reads the text.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ish.application.sources import Sources
from ish.domain.chunk import Chunk


class ChunkSourceError(Exception):
    """Report a source that cannot be reached, refuses a call, or answers badly."""


@runtime_checkable
class ChunkSource(Protocol):
    """Contract for searching an index that another machine holds."""

    @property
    def host(self) -> str:
        """Name of the machine that answers, for a line that says who did."""
        ...

    @property
    def version(self) -> str:
        """Version the source reports. Empty before the first call."""
        ...

    def search(
        self,
        query: str,
        limit: int,
        shape: str,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
    ) -> str:
        """Return the ranked results of *query* as lines in *shape*.

        The source writes each line, because it knows the tree. Return an
        empty text when nothing matches.
        """
        ...

    def sources(
        self,
        query: str,
        limit: int,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
    ) -> Sources:
        """Return the ranked chunks of *query* with the text of each.

        Return also the meaning of each acronym the texts use that the
        tree spells out, with the chunk that writes it.
        """
        ...

    def link(self, chunk: Chunk) -> str:
        """Return the URL the source gave for *chunk*."""
        ...

    def kind_of(self, chunk: Chunk) -> str:
        """Return the type the source gave for *chunk*: code, doc, test, config."""
        ...
