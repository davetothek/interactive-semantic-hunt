"""The matrix file beside an index: its chunk ids and vectors, mapped.

Reading every blob out of SQLite and joining them cost a process 310 to
460 ms for 224 MB of vectors. Mapping the same bytes cost 18 ms. The file
is a cache. It carries a stamp of the index state it was built from, and
a file that does not match is read past and written again.
"""

import contextlib
import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# The matrix file beside an index: a header of this many bytes, then
# the chunk ids as int64, then the vectors as float32, one row each.
# The header is JSON, padded with spaces, and names the format, the
# model, the width, the row count, and the stamp of the index state.
MATRIX_SUFFIX = "-matrix"
MATRIX_FORMAT = 1
MATRIX_HEADER_BYTES = 4096


class MatrixFile:
    """Map and write the matrix file of one index, for one model."""

    def __init__(self, path: Path, model_id: str) -> None:
        self.path = path
        self.model_id = model_id
        # The index state the file was last mapped or written under.
        self.saved: tuple[int, int] | None = None

    def map(self, width: int, stamp: tuple[int, int]):
        """Map the matrix file beside the index, or return None.

        Return None for a file that is absent, that describes another
        model, another width, or another state of the index, or that is
        not the size its header claims. The blobs are read then, and
        the file is written again.
        """
        import numpy

        path = self.path
        try:
            with path.open("rb") as handle:
                header = json.loads(handle.read(MATRIX_HEADER_BYTES))
            size = path.stat().st_size
        except (OSError, ValueError):
            return None
        if not isinstance(header, dict):
            return None
        expected = {
            "format": MATRIX_FORMAT,
            "model_id": self.model_id,
            "dim": width,
            "stamp": list(stamp),
        }
        if {key: header.get(key) for key in expected} != expected:
            return None
        rows = header.get("rows")
        if not isinstance(rows, int) or rows < 1:
            return None
        if size != MATRIX_HEADER_BYTES + rows * (8 + 4 * width):
            return None
        try:
            identifiers = numpy.memmap(
                path,
                dtype=numpy.int64,
                mode="r",
                offset=MATRIX_HEADER_BYTES,
                shape=(rows,),
            )
            matrix = numpy.memmap(
                path,
                dtype=numpy.float32,
                mode="r",
                offset=MATRIX_HEADER_BYTES + rows * 8,
                shape=(rows, width),
            )
        except (OSError, ValueError) as exc:
            log.debug("Cannot map the matrix beside %s: %s", path, exc)
            return None
        self.saved = stamp
        return identifiers, matrix

    def save(
        self, stamp: tuple[int, int], identifiers: Sequence[int], matrix: Any
    ) -> None:
        """Write the matrix file beside the index, for the next process.

        Write a temporary file and rename it into place, so a reader
        never maps a file that is half written. A file that cannot be
        written costs nothing but the blobs it would have saved.
        """
        import numpy

        header = json.dumps(
            {
                "format": MATRIX_FORMAT,
                "model_id": self.model_id,
                "dim": int(matrix.shape[1]),
                "rows": int(matrix.shape[0]),
                "stamp": list(stamp),
            }
        ).encode("utf-8")
        if len(header) > MATRIX_HEADER_BYTES:
            return
        path = self.path
        partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
        try:
            with partial.open("wb") as handle:
                handle.write(header.ljust(MATRIX_HEADER_BYTES))
                # Write the memory of each array. A copy of the matrix as
                # bytes costs as much again as the write itself.
                handle.write(
                    numpy.ascontiguousarray(identifiers, dtype=numpy.int64).data
                )
                handle.write(numpy.ascontiguousarray(matrix, dtype=numpy.float32).data)
            os.replace(partial, path)
        except OSError as exc:
            log.debug("Cannot write the matrix beside %s: %s", path, exc)
            with contextlib.suppress(OSError):
                partial.unlink()
            return
        self.saved = stamp


def joined(rows: Sequence[tuple[int, int, bytes]], width: int):
    """Return the ids of *rows* and their vectors as one matrix."""
    import numpy

    matrix = numpy.frombuffer(
        b"".join(row[2] for row in rows), dtype=numpy.float32
    ).reshape(len(rows), width)
    return [int(row[0]) for row in rows], matrix
