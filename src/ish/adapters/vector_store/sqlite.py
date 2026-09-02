"""SQLite adapter for the VectorStore protocol.

Hold chunks and their embeddings in one file, so a repeated query reuses
the work of the last one. Key vectors by content hash and model, so a
renamed file or an untouched definition never needs embedding again.

Store where a chunk is, never what it says. An index that held the
source would be a second readable copy of it, outside whatever protects
the repository, and outliving it. Read the text from the file when a
preview needs it.

Store every vector at unit length. Cosine similarity is then a dot
product, which halves the work in the search loop.

The TUI indexes on a worker thread and searches on another, so the
connection is opened for cross-thread use and every statement runs under
one lock. SQLite itself serializes writers; the lock is what keeps a
single connection safe to share.
"""

import array
import logging
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from ish.application.ports.vector_store import (
    LEXICAL_WEIGHT,
    SEMANTIC_WEIGHT,
    FileStamp,
    fuse_rankings,
    is_code_like,
    split_identifier,
)
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

# Bump when the schema changes, and also when anything changes the meaning
# of a stored vector, such as the task prefixes the embedder applies. An
# index built by an older version is discarded rather than mixed.
SCHEMA_VERSION = "4"

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")

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
    terms        TEXT NOT NULL,
    start_line   INTEGER NOT NULL,
    end_line     INTEGER NOT NULL
);

CREATE INDEX chunks_by_path ON chunks(path);
CREATE INDEX chunks_by_hash ON chunks(content_hash);

-- Lexical half of the search. Keep the content in chunks and mirror it
-- here, so an exact identifier is findable when a vector misses it.
-- porter stems prose; unicode61 splits on the underscore, so a snake
-- case name matches both whole and in parts.
-- Lexical matching over the names only. The body is not stored, and
-- the lexical half runs only for a query that names something, which
-- matches on these columns anyway.
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    symbol, terms,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, symbol, terms)
    VALUES (new.id, new.symbol, new.terms);
END;

CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, symbol, terms)
    VALUES ('delete', old.id, old.symbol, old.terms);
END;

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


def _fts_query(text: str) -> str:
    """Turn user text into a safe FTS5 match expression.

    Quote every word and join them with OR, so punctuation in the query
    cannot be read as FTS5 syntax and any single word can still match.
    """
    words = _WORD_RE.findall(text)
    return " OR ".join(f'"{word}"' for word in words)


class SqliteVectorStore:
    """Persist chunks and embeddings in a single SQLite file."""

    def __init__(
        self, db_path: Path, *, model_id: str, root: Path | None = None
    ) -> None:
        self._model_id = model_id
        self._path = db_path
        self._root = root
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # The TUI reaches the store from a worker thread, so the connection
        # may not stay bound to the thread that opened it.
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
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
                self._record_root()
                return
            log.info("Index schema changed. Rebuilding %s", self._path)
            for trigger in ("chunks_fts_insert", "chunks_fts_delete"):
                self._db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            for table in ("chunks_fts", "vectors", "chunks", "files", "meta"):
                self._db.execute(f"DROP TABLE IF EXISTS {table}")
            self._db.commit()
            # Dropping a table frees its pages but leaves what they held.
            # An index built before the source stopped being stored would
            # keep that source readable on disk for ever.
            self._db.execute("VACUUM")

        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._db.commit()
        self._record_root()

    def _record_root(self) -> None:
        """Store the tree this index was built from.

        The file name carries only a hash of the path, so without this
        no one can tell which tree an index describes.
        """
        if self._root is None:
            return
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('root', ?)",
                (str(self._root),),
            )

    @staticmethod
    def read_root(db_path: Path) -> Path | None:
        """Return the tree an index was built from, without opening it fully."""
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        try:
            row = db.execute("SELECT value FROM meta WHERE key = 'root'").fetchone()
        except sqlite3.Error:
            return None
        finally:
            db.close()
        return Path(row[0]) if row else None

    def clear(self) -> None:
        """Discard every indexed file.

        Keep the vectors. Re-indexing then costs parsing, not embedding.
        """
        with self._lock, self._db:
            self._db.execute("DELETE FROM files")

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
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
        with self._lock:
            rows = self._db.execute("SELECT path, mtime_ns, size FROM files").fetchall()
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
        lock = self._lock
        # Stay well inside the SQLite variable limit for a large index.
        for start in range(0, len(ordered), 500):
            batch = ordered[start : start + 500]
            marks = ",".join("?" * len(batch))
            with lock:
                rows = self._db.execute(
                    f"SELECT content_hash FROM vectors "  # noqa: S608
                    f"WHERE model_id = ? AND content_hash IN ({marks})",
                    [self._model_id, *batch],
                ).fetchall()
            present.update(row[0] for row in rows)
        return wanted - present

    def add_vectors(self, vectors: Mapping[str, Sequence[float]]) -> None:
        """Store vectors by content hash, at unit length."""
        if not vectors:
            return
        rows = [
            (digest, self._model_id, len(vector), _pack(vector))
            for digest, vector in vectors.items()
        ]
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors "
                "(content_hash, model_id, dim, data) VALUES (?, ?, ?, ?)",
                rows,
            )

    def set_file(
        self, path: Path, stamp: FileStamp, chunks: Sequence[tuple[Chunk, str]]
    ) -> None:
        """Replace everything held for *path*."""
        text = str(path)
        with self._lock, self._db:
            self._db.execute("DELETE FROM chunks WHERE path = ?", (text,))
            self._db.execute(
                "INSERT OR REPLACE INTO files (path, mtime_ns, size) VALUES (?, ?, ?)",
                (text, stamp.mtime_ns, stamp.size),
            )
            self._db.executemany(
                "INSERT INTO chunks "
                "(path, content_hash, kind, language, symbol, terms, "
                "start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        text,
                        digest,
                        chunk.kind,
                        chunk.language,
                        chunk.symbol,
                        split_identifier(chunk.symbol or ""),
                        chunk.start_line,
                        chunk.end_line,
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
        with self._lock, self._db:
            self._db.executemany(
                "DELETE FROM files WHERE path = ?", [(str(p),) for p in paths]
            )

    def prune_vectors(self) -> int:
        """Delete vectors no chunk references. Return how many went."""
        with self._lock, self._db:
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
        with self._lock:
            rows = self._db.execute(
                "SELECT path, kind, language, symbol, start_line, end_line "
                "FROM chunks ORDER BY path, start_line"
            ).fetchall()
        return [self._to_chunk(row) for row in rows]

    @staticmethod
    def _to_chunk(row: Sequence[Any]) -> Chunk:
        """Build a Chunk from the stored column order.

        Leave the text empty. The index holds no source, so a caller
        that needs it reads the file.
        """
        path, kind, language, symbol, start, end = row[:6]
        return Chunk(
            path=Path(str(path)),
            text="",
            kind=str(kind),
            language=str(language),
            symbol=None if symbol is None else str(symbol),
            start_line=int(start),
            end_line=int(end),
        )

    def search(
        self,
        query_vector: Sequence[float],
        query_text: str = "",
        limit: int = 5,
        keep: Callable[[Chunk], bool] | None = None,
    ) -> Sequence[tuple[Chunk, float]]:
        """Rank chunks by vector similarity, fused with a lexical order."""
        # Over-fetch, because a filter and the lexical half both trim.
        scored = self._semantic(query_vector, max(limit * 8, 60))
        if keep is not None:
            scored = [pair for pair in scored if keep(pair[0])]
        if not query_text or not is_code_like(query_text):
            return scored[:limit]

        lexical = self._lexical(query_text, limit=max(limit * 4, 20))
        if keep is not None:
            lexical = [chunk for chunk in lexical if keep(chunk)]
        if not lexical:
            return scored[:limit]

        # Fuse over a wider slice than the caller asked for, so a chunk
        # ranked well only by the lexical half can still surface.
        candidates = [chunk for chunk, _ in scored[: max(limit * 4, 20)]]
        by_chunk = dict(scored)
        fused = fuse_rankings(
            [(candidates, SEMANTIC_WEIGHT), (lexical, LEXICAL_WEIGHT)], limit
        )
        return [(chunk, by_chunk.get(chunk, 0.0)) for chunk in fused]

    def _lexical(self, query_text: str, limit: int) -> list[Chunk]:
        """Return chunks matching *query_text*, best first, by BM25."""
        match = _fts_query(query_text)
        if not match:
            return []

        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT c.path, c.kind, c.language, c.symbol, "
                    "       c.start_line, c.end_line "
                    "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
                    "WHERE chunks_fts MATCH ? "
                    "ORDER BY bm25(chunks_fts, 2.0, 1.0) "
                    "LIMIT ?",
                    (match, limit),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            # A malformed match expression must not fail the whole query.
            log.debug("Lexical search skipped: %s", exc)
            return []

        return [self._to_chunk(row) for row in rows]

    def _semantic(
        self, query_vector: Sequence[float], top: int
    ) -> list[tuple[Chunk, float]]:
        """Return the *top* best chunks for a query vector.

        Read only the vectors, score them as one matrix, then read the
        details of the few that won. Fetching every row's text and
        building a chunk for each cost far more than the arithmetic:
        measured on 7000 chunks, the multiplication took 1 ms while
        materializing every row took 60.
        """
        norm = math.sqrt(sum(value * value for value in query_vector))
        if norm == 0.0 or top <= 0:
            return []

        with self._lock:
            rows = self._db.execute(
                "SELECT c.id, v.dim, v.data "
                "FROM chunks c "
                "JOIN vectors v ON v.content_hash = c.content_hash "
                "WHERE v.model_id = ?",
                (self._model_id,),
            ).fetchall()
        if not rows:
            return []

        width = len(query_vector)
        for _identifier, dim, _data in rows:
            if dim != width:
                raise ValueError(
                    f"The index holds {dim}-dimension vectors but the query has "
                    f"{width}. Re-index with the current model."
                )

        import numpy

        matrix = numpy.frombuffer(
            b"".join(row[2] for row in rows), dtype=numpy.float32
        ).reshape(len(rows), width)
        query = numpy.asarray(query_vector, dtype=numpy.float32) / norm
        scores = matrix @ query

        count = min(top, len(rows))
        best = numpy.argpartition(-scores, count - 1)[:count]
        best = best[numpy.argsort(-scores[best])]

        order = [int(rows[index][0]) for index in best]
        found = {int(rows[index][0]): float(scores[index]) for index in best}
        return self._details(found, order)

    def _details(
        self, scores: dict[int, float], order: list[int]
    ) -> list[tuple[Chunk, float]]:
        """Read the chunks behind the identifiers that scored best."""
        marks = ",".join("?" * len(order))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, path, kind, language, symbol, start_line, end_line "
                f"FROM chunks WHERE id IN ({marks})",  # noqa: S608
                order,
            ).fetchall()

        by_id = {int(row[0]): self._to_chunk(row[1:]) for row in rows}
        return [(by_id[key], scores[key]) for key in order if key in by_id]
