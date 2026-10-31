"""Link a chunk to where a web server or a share publishes its tree."""

import urllib.parse
from pathlib import Path

from ish.adapters.link.file_uri import FileUri
from ish.domain.chunk import Chunk


class BaseUrl:
    """Join a base URL and the path of a chunk relative to a tree root."""

    def __init__(self, base: str, root: Path) -> None:
        """Publish the files under *root* at *base*.

        A chunk outside *root* has no published address, so link it to
        its file on disk rather than invent a URL that does not resolve.
        """
        self._base = base.rstrip("/")
        self._root = root
        self._outside = FileUri()

    def link(self, chunk: Chunk) -> str:
        try:
            relative = chunk.path.relative_to(self._root)
        except ValueError:
            return self._outside.link(chunk)
        path = urllib.parse.quote(relative.as_posix())
        return f"{self._base}/{path}#{chunk.location.fragment()}"
