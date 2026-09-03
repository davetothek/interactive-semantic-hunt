"""Keep a chunk inside what the embedding model can read.

An embedding model reads a fixed number of tokens and drops the rest
without saying so, which makes a large chunk mostly unsearchable while
looking indexed. Measured on one firmware tree: 158 chunks held 9 million
characters past the window, 53 percent of all the C and C++ text.

Wrap a parser rather than teaching each one, so every language gets the
same treatment and a plugin gets it without asking.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

# Roughly the text a 2048-token context holds, which is what Ollama
# serves an embedding model by default. A chunk longer than this is
# read only as far as the window reaches.
MAX_CHUNK_CHARS = 8_000


class SizeLimited:
    """Split any chunk a parser returns that the model cannot read whole.

    Divide on line boundaries so a piece stays readable, and keep the
    symbol, because each piece still belongs to the definition it came
    from. The line range tells the pieces apart.
    """

    def __init__(self, inner, limit: int = MAX_CHUNK_CHARS) -> None:
        self._inner = inner
        self._limit = limit
        self.language = inner.language
        self.suffixes = inner.suffixes

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Parse, then divide whatever came back too large to read."""
        out: list[Chunk] = []
        for chunk in self._inner.parse(path, source):
            if len(chunk.text) <= self._limit:
                out.append(chunk)
                continue
            pieces = list(self._divide(chunk))
            log.debug(
                "%s: %s holds %d characters, split into %d",
                path,
                chunk.symbol,
                len(chunk.text),
                len(pieces),
            )
            out.extend(pieces)
        return out

    def _divide(self, chunk: Chunk):
        """Yield the chunk as pieces that each fit the window."""
        lines = chunk.text.splitlines(keepends=True)
        start = chunk.start_line
        held: list[str] = []
        size = 0

        for line in lines:
            # A single line longer than the window cannot be divided on a
            # boundary, so let it stand alone and be read as far as it
            # reaches. Breaking mid-token would only garble it.
            if size and size + len(line) > self._limit:
                yield self._piece(chunk, held, start)
                start += len(held)
                held, size = [], 0
            held.append(line)
            size += len(line)

        if held:
            yield self._piece(chunk, held, start)

    @staticmethod
    def _piece(chunk: Chunk, lines: list[str], start: int) -> Chunk:
        """Build one piece covering *lines*, beginning at *start*."""
        return Chunk(
            path=chunk.path,
            text="".join(lines),
            kind=chunk.kind,
            language=chunk.language,
            symbol=chunk.symbol,
            start_line=start,
            end_line=start + len(lines) - 1,
        )
