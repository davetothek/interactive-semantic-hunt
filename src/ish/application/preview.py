"""Read the source a chunk points at.

The index stores where a chunk is, not what it says. Keeping the text
out of the index means the index holds no readable copy of the source,
and a preview always shows the file as it is now rather than as it was
when it was indexed.
"""

import logging

from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)


def load_text(chunk: Chunk) -> str:
    """Return the lines a chunk covers, read from the file.

    Return the chunk's own text when it already carries some, which is
    the case for a chunk that has just been parsed. Explain the problem
    in place of the source when the file cannot be read, so a preview
    shows a reason rather than nothing.
    """
    if chunk.text:
        return chunk.text

    try:
        lines = chunk.path.read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        return f"{chunk.path} no longer exists.\nRe-index to drop it from results."
    except (OSError, UnicodeDecodeError) as exc:
        return f"Cannot read {chunk.path}: {exc}"

    if chunk.start_line > len(lines):
        return (
            f"{chunk.path} has changed since it was indexed.\n"
            f"Re-index to refresh the line numbers."
        )
    return "".join(lines[chunk.start_line - 1 : chunk.end_line])
