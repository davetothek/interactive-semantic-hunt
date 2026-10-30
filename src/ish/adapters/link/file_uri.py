"""Link a chunk to its file on the local disk."""

from ish.domain.chunk import Chunk


class FileUri:
    """Return a ``file://`` URI with the location as its fragment."""

    def link(self, chunk: Chunk) -> str:
        return f"{chunk.path.as_uri()}#{chunk.location.fragment()}"
