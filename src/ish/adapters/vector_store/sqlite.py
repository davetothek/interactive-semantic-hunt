"""SQLite adapter for the VectorStore protocol.

Hold chunks and their embeddings in one file, so a repeated query reuses
the work of the last one. Key vectors by content hash and model, so a
renamed file or an untouched definition never needs embedding again.

Store every vector at unit length. Cosine similarity is then a dot
product, which halves the work in the search loop.
"""

import array
import logging
import math
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from operator import mul
from pathlib import Path
from typing import Any

from ish.application.ports.vector_store import FileStamp
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE files (
    path     TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size     INTEGER NOT NULL
);

CREATE TABLE chunks (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    kind         TEXT NOT NULL,
    language     TEXT NOT NULL,
    symbol       TEXT,
    start_line   INTEGER NOT NULL,
    end_line     INTEGER NOT NULL,
    text         TEXT NOT NULL
);

CREATE INDEX chunks_by_path ON chunks(path);
CREATE INDEX chunks_by_hash ON chunks(content_hash);

CREATE TABLE vectors (
    content_hash TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    data         BLOB NOT NULL,
    PRIMARY KEY (content_hash, model_id)
);
"""


def _pack(vector: Sequence[float]) -> bytes:
    """Normalize a vector to unit length and pack it as float32."""
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return array.array("f", vector).tobytes()
    return array.array("f", [value / norm for value in vector]).tobytes()


def _unpack(blob: bytes) -> array.array:
    """Decode a packed float32 vector."""
    values = array.array("f")
    values.frombytes(blob)
    return values


class SqliteVectorStore:
    """Persist chunks and embeddings in a single SQLite file."""

    def __init__(self, db_path: Path, *, model_id: str) -> None:
        self._model_id = model_id
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = sqlite3.connect(db_path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._prepare()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _prepare(self) -> None:
        """Create the schema, discarding any build from an older version."""
        found = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()

        if found:
            row = self._db.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row and row[0] == SCHEMA_VERSION:
                return
            log.info("Index schema changed. Rebuilding %s", self._path)
            for table in ("vectors", "chunks", "files", "meta"):
                self._db.execute(f"DROP TABLE IF EXISTS {table}")

        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._db.commit()

    def clear(self) -> None:
        """Discard every indexed file.

        Keep the vectors. Re-indexing then costs parsing, not embedding.
        """
        with self._db:
            self._db.execute("DELETE FROM files")

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def __enter__(self) -> SqliteVectorStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Index maintenance
    # ------------------------------------------------------------------

    def file_stamps(self) -> Mapping[Path, FileStamp]:
        """Return the stamp held for every indexed file."""
        rows = self._db.execute("SELECT path, mtime_ns, size FROM files")
        return {
            Path(path): FileStamp(mtime_ns=mtime_ns, size=size)
            for path, mtime_ns, size in rows
        }

    def missing_vectors(self, hashes: Collection[str]) -> set[str]:
        """Return the subset of *hashes* with no vector for this model."""
        wanted = set(hashes)
        if not wanted:
            return set()

        present: set[str] = set()
        ordered = list(wanted)
        # Stay well inside the SQLite variable limit for a large index.
        for start in range(0, len(ordered), 500):
            batch = ordered[start : start + 500]
            marks = ",".join("?" * len(batch))
            rows = self._db.execute(
                f"SELECT content_hash FROM vectors "  # noqa: S608
                f"WHERE model_id = ? AND content_hash IN ({marks})",
                [self._model_id, *batch],
            )
            present.update(row[0] for row in rows)
        return wanted - present

    def add_vectors(self, vectors: Mapping[str, Sequence[float]]) -> None:
        """Store vectors by content hash, at unit length."""
        if not vectors:
            return
        self._db.executemany(
            "INSERT OR REPLACE INTO vectors (content_hash, model_id, dim, data) "
            "VALUES (?, ?, ?, ?)",
            [
                (digest, self._model_id, len(vector), _pack(vector))
                for digest, vector in vectors.items()
            ],
        )
        self._db.commit()

    def set_file(
        self, path: Path, stamp: FileStamp, chunks: Sequence[tuple[Chunk, str]]
    ) -> None:
        """Replace everything held for *path*."""
        text = str(path)
        with self._db:
            self._db.execute("DELETE FROM chunks WHERE path = ?", (text,))
            self._db.execute(
                "INSERT OR REPLACE INTO files (path, mtime_ns, size) VALUES (?, ?, ?)",
                (text, stamp.mtime_ns, stamp.size),
            )
            self._db.executemany(
                "INSERT INTO chunks "
                "(path, content_hash, kind, language, symbol, start_line, end_line, "
                "text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        text,
                        digest,
                        chunk.kind,
                        chunk.language,
                        chunk.symbol,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.text,
                    )
                    for chunk, digest in chunks
                ],
            )

    def remove_files(self, paths: Collection[Path]) -> None:
        """Drop everything held for *paths*.

        Leave the vectors in place. They are small, and keeping them
        means restoring a deleted file costs no embedding.
        """
        if not paths:
            return
        with self._db:
            self._db.executemany(
                "DELETE FROM files WHERE path = ?", [(str(p),) for p in paths]
            )

    def prune_vectors(self) -> int:
        """Delete vectors no chunk references. Return how many went."""
        with self._db:
            cursor = self._db.execute(
                "DELETE FROM vectors WHERE content_hash NOT IN "
                "(SELECT content_hash FROM chunks)"
            )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def chunks(self) -> Sequence[Chunk]:
        """Return every chunk the store holds, ordered by path then line."""
        rows = self._db.execute(
            "SELECT path, text, kind, language, symbol, start_line, end_line "
            "FROM chunks ORDER BY path, start_line"
        )
        return [self._to_chunk(row) for row in rows]

    @staticmethod
    def _to_chunk(row: Sequence[Any]) -> Chunk:
        """Build a Chunk from the stored column order."""
        path, text, kind, language, symbol, start, end = row[:7]
        return Chunk(
            path=Path(str(path)),
            text=str(text),
            kind=str(kind),
            language=str(language),
            symbol=None if symbol is None else str(symbol),
            start_line=int(start),
            end_line=int(end),
        )

    def search(
        self, query_vector: Sequence[float], limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        """Find the *limit* most similar chunks to the *query_vector*."""
        norm = math.sqrt(sum(value * value for value in query_vector))
        if norm == 0.0:
            return []
        query = array.array("f", [value / norm for value in query_vector])

        rows = self._db.execute(
            "SELECT c.path, c.text, c.kind, c.language, c.symbol, "
            "       c.start_line, c.end_line, v.data, v.dim "
            "FROM chunks c "
            "JOIN vectors v ON v.content_hash = c.content_hash "
            "WHERE v.model_id = ?",
            (self._model_id,),
        )

        scored: list[tuple[Chunk, float]] = []
        for row in rows:
            blob, dim = row[7], row[8]
            if dim != len(query):
                raise ValueError(
                    f"The index holds {dim}-dimension vectors but the query has "
                    f"{len(query)}. Re-index with the current model."
                )
            score = sum(map(mul, query, _unpack(blob), strict=True))
            scored.append((self._to_chunk(row), score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]
