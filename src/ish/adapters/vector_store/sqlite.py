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

Keep the matrix a search scores in a file beside the index as well, and
map it into memory. Reading every blob out of SQLite and joining them
cost a process 310 to 460 ms for 224 MB of vectors. Mapping the same
bytes cost 18 ms. SQLite stays the source of truth: the file carries a
stamp of the index state it was built from, and a file that does not
match is read past and written again.

Keep the vectors of one repository in one shared store, which every
index of that repository attaches. A vector is keyed by the hash of the
text it stands for, so a second checkout of the repository finds the
vectors of the first and embeds only what differs. Chunk rows and file
stamps stay in the index of each tree, because a result names a file to
open, and a shared row would open the wrong checkout.

The TUI indexes on a worker thread and searches on another, so the
connection is opened for cross-thread use and every statement runs under
one lock. SQLite itself serializes writers; the lock is what keeps a
single connection safe to share.
"""

import array
import contextlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from ish.application.ports.vector_store import FileStamp, StoreBusy
from ish.application.ranking import ResultFilter, rank, split_identifier
from ish.domain.chunk import Chunk
from ish.domain.match import Match

log = logging.getLogger(__name__)

# Bump when the schema changes, and also when anything changes the meaning
# of a stored vector, such as the task prefixes the embedder applies. An
# index built by an older version is discarded rather than mixed, except
# one at MIGRATES_FROM, which keeps its vectors and moves them into the
# shared store of its repository.
SCHEMA_VERSION = "5"
MIGRATES_FROM = "4"

# The shared store of a repository, named in the index directory after a
# digest of the repository's common directory.
SHARED_PREFIX = "vectors-"

# Read chunk details this many at a time while a filter is trimming the
# vector order. Reading a detail costs far more than scoring a vector,
# so the slice grows only as far as the filter makes it.
DETAIL_BATCH = 64

# The matrix file beside an index: a header of this many bytes, then
# the chunk ids as int64, then the vectors as float32, one row each.
# The header is JSON, padded with spaces, and names the format, the
# model, the width, the row count, and the stamp of the index state.
MATRIX_SUFFIX = "-matrix"
MATRIX_FORMAT = 1
MATRIX_HEADER_BYTES = 4096

# How long after its last write a store waits before it writes the
# matrix file on a cache miss. An index run commits every few seconds,
# and a 100 MB file behind each commit would slow the picker searching
# beside it. A store that wrote writes the file when it closes instead.
SETTLE_SECONDS = 10.0

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

"""

# The vectors, keyed by the text they stand for and the model that made
# them. The table lives in the shared store of the repository, or in the
# index itself for a tree outside any repository.
_VECTORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS {schema}.vectors (
    content_hash TEXT NOT NULL,
    model_id     TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    data         BLOB NOT NULL,
    PRIMARY KEY (content_hash, model_id)
);
"""

# What a shared store says about itself, so a reader of the directory
# can tell it from an index.
_SHARED_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared.meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO shared.meta (key, value) VALUES ('kind', 'vectors');
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


BUSY_SECONDS = 2.0
"""How long to wait for a lock before the store says the index is busy.

Long enough for a commit in flight to land. Short enough that a caller
hears an answer. One index run held a call for 1800 s, and the caller
gave up with nothing to act on.
"""


def _referenced_by(index_path: Path) -> set[str] | None:
    """Return the content hashes the index at *index_path* references.

    Return an empty set for an index that is gone, and None for one
    that is there but cannot be read.
    """
    if not index_path.exists():
        return set()
    try:
        db = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = db.execute("SELECT DISTINCT content_hash FROM chunks").fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return {str(row[0]) for row in rows}


def _busy(path: Path, exc: sqlite3.OperationalError) -> Exception:
    """Return the error to raise for *exc*, named when a writer holds the file."""
    text = str(exc)
    if "lock" not in text and "busy" not in text:
        return exc
    return StoreBusy(
        f"Another process holds the index {path}. "
        f"An index run keeps the file until the run ends."
    )


class SqliteVectorStore:
    """Persist chunks and embeddings in a single SQLite file."""

    def __init__(
        self,
        db_path: Path,
        *,
        model_id: str,
        root: Path | None = None,
        repository: Path | None = None,
        checkout: Path | None = None,
        shared: Path | None = None,
        siblings: Sequence[Path] = (),
    ) -> None:
        """Open the index at *db_path* for the tree at *root*.

        Name the *repository* the tree belongs to and the *checkout* it
        is in when they are known. Both are recorded, so a later search
        can tell two checkouts of one repository apart without asking
        git again.

        Keep the vectors in *shared*, the store of the repository, when
        one is given, and in this index otherwise. An index that has
        moved its vectors into a shared store reaches it again on its
        own. *siblings* names the other indexes of the repository, which
        a sweep of the shared store asks before it deletes anything.
        """
        self._model_id = model_id
        self._path = db_path
        self._identity = {
            "root": root,
            "repository": repository,
            "checkout": checkout,
        }
        self._shared = shared
        self._siblings = list(siblings)
        # Where the vectors are, as SQL names the table.
        self._vectors = "main.vectors"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # The TUI reaches the store from a worker thread, so the connection
        # may not stay bound to the thread that opened it.
        self._lock = threading.RLock()
        # The scored matrix, kept between queries. Reading every vector
        # out of SQLite and joining it cost more than the arithmetic:
        # measured on 23,215 chunks, 118 ms a query against 2 ms.
        self._matrix_cache: tuple[tuple[int, int], Sequence[int], Any] | None = None
        self._writes = 0
        # When this store last wrote, and the index state the matrix
        # file beside it was last read or written under.
        self._last_write = 0.0
        self._saved: tuple[int, int] | None = None
        # A second close finds the connection gone, so it does nothing.
        self._closed = False
        self._matrix_path = db_path.with_name(db_path.name + MATRIX_SUFFIX)
        self._db = sqlite3.connect(
            db_path, timeout=BUSY_SECONDS, check_same_thread=False
        )
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._prepare()
        except sqlite3.OperationalError as exc:
            # Opening is where a caller met the writer and waited. Say
            # which file is held, and let the caller decide what to do.
            self._db.close()
            raise _busy(db_path, exc) from exc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _prepare(self) -> None:
        """Create or migrate the schema, discarding a build this version cannot read."""
        found = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        meta: dict[str, str] = {}
        if found:
            meta = dict(self._db.execute("SELECT key, value FROM main.meta").fetchall())
        # An index that moved its vectors into a shared store names it,
        # so the store is reached again when nothing else names it.
        if self._shared is None and "shared" in meta:
            self._shared = Path(meta["shared"])
        self._attach()

        version = meta.get("schema_version")
        if found and version == SCHEMA_VERSION:
            self._record_identity()
            return
        if found and version == MIGRATES_FROM:
            self._migrate_vectors()
            self._record_identity()
            return
        if found:
            log.info("Index schema changed. Rebuilding %s", self._path)
            for trigger in ("chunks_fts_insert", "chunks_fts_delete"):
                self._db.execute(f"DROP TRIGGER IF EXISTS main.{trigger}")
            for table in ("chunks_fts", "vectors", "chunks", "files", "meta"):
                self._db.execute(f"DROP TABLE IF EXISTS main.{table}")
            self._db.commit()
            # Dropping a table frees its pages but leaves what they held.
            # An index built before the source stopped being stored would
            # keep that source readable on disk for ever.
            self._db.execute("VACUUM main")

        self._db.executescript(_SCHEMA)
        if self._shared is None:
            self._db.executescript(_VECTORS_SCHEMA.format(schema="main"))
        self._db.execute(
            "INSERT INTO main.meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._record_shared()
        self._db.commit()
        self._record_identity()

    def _attach(self) -> None:
        """Reach the shared store, when this index has one, and make sure it exists."""
        if self._shared is None:
            self._vectors = "main.vectors"
            return
        self._shared.parent.mkdir(parents=True, exist_ok=True)
        self._db.execute("ATTACH DATABASE ? AS shared", (str(self._shared),))
        self._db.execute("PRAGMA shared.journal_mode=WAL")
        self._db.execute("PRAGMA shared.synchronous=NORMAL")
        made = self._db.execute(
            "SELECT name FROM shared.sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if made is None:
            # A new store. Say what it is and whose it is, this once.
            # Opening writes nothing after that, as for an index.
            self._db.executescript(
                _VECTORS_SCHEMA.format(schema="shared") + _SHARED_META_SCHEMA
            )
            repository = self._identity.get("repository")
            if repository is not None:
                self._db.execute(
                    "INSERT OR IGNORE INTO shared.meta (key, value) "
                    "VALUES ('repository', ?)",
                    (str(repository),),
                )
            self._db.commit()
        self._vectors = "shared.vectors"

    def _record_shared(self) -> None:
        """Name the shared store in meta, so a later opening finds it unaided."""
        if self._shared is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO main.meta (key, value) VALUES ('shared', ?)",
            (str(self._shared),),
        )

    def _migrate_vectors(self) -> None:
        """Bring an index at MIGRATES_FROM to this version, keeping every vector.

        Copy the vectors into the shared store of the repository and
        drop the table they came from. An index outside any repository
        keeps its table. Nothing is embedded again either way: a vector
        is a second of embedding, and an index holds tens of thousands.
        """
        if self._shared is not None:
            moved = self._db.execute(
                f"INSERT OR IGNORE INTO {self._vectors} "
                "SELECT content_hash, model_id, dim, data FROM main.vectors"
            ).rowcount
            self._db.commit()
            log.info("Moved %d vectors of %s into %s", moved, self._path, self._shared)
            self._db.execute("DROP TABLE main.vectors")
            self._record_shared()
        self._db.execute(
            "UPDATE main.meta SET value = ? WHERE key = 'schema_version'",
            (SCHEMA_VERSION,),
        )
        self._db.commit()
        if self._shared is not None:
            # The pages the table held stay in the file until a vacuum.
            self._db.execute("VACUUM main")

    def _record_identity(self) -> None:
        """Store the tree this index was built from, and where it belongs.

        The file name carries only a hash of the path, so without this
        no one can tell which tree an index describes, nor which
        repository and checkout hold it.

        Write only what differs from what is stored. Opening an index is
        what a search, a status report, and a refresh of an unchanged
        tree all do, and a write there takes the write lock of every
        index the caller touches. It also moves ``PRAGMA data_version``,
        which makes a reader in another process discard the scored
        matrix it holds.
        """
        wanted = {
            key: str(value)
            for key, value in self._identity.items()
            if value is not None
        }
        if not wanted:
            return
        with self._lock:
            stored = dict(
                self._db.execute(
                    "SELECT key, value FROM meta "
                    "WHERE key IN ('root', 'repository', 'checkout')"
                ).fetchall()
            )
            changed = {
                key: value for key, value in wanted.items() if stored.get(key) != value
            }
            if not changed:
                return
            with self._db:
                self._db.executemany(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    list(changed.items()),
                )

    @staticmethod
    def read_identity(db_path: Path) -> dict[str, Path]:
        """Return the tree, repository, and checkout an index records.

        Read without opening the index fully, and return what is there:
        an index made before repositories were recorded names only its
        tree, and a file that is not an index names nothing.
        """
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return {}
        try:
            rows = db.execute(
                "SELECT key, value FROM meta "
                "WHERE key IN ('root', 'repository', 'checkout')"
            ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            db.close()
        return {str(key): Path(str(value)) for key, value in rows}

    @staticmethod
    def read_root(db_path: Path) -> Path | None:
        """Return the tree an index was built from, without opening it fully."""
        return SqliteVectorStore.read_identity(db_path).get("root")

    def clear(self) -> None:
        """Discard every indexed file.

        Keep the vectors. Re-indexing then costs parsing, not embedding.
        """
        with self._lock, self._db:
            self._mark_written()
            self._db.execute("DELETE FROM files")

    def chunking(self) -> str:
        """Return the chunking stamp the files were read under, or empty."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM meta WHERE key = 'chunking'"
            ).fetchone()
        return "" if row is None else str(row[0])

    def set_chunking(self, stamp: str) -> None:
        """Record the chunking stamp the files are read under from now on."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('chunking', ?)",
                (stamp,),
            )

    def close(self) -> None:
        """Close the database connection.

        Leave the matrix file current first. A store that wrote held
        the file back while it wrote, and the next process would read
        the blobs again.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._save_matrix_if_current()
            self._matrix_cache = None
            self._db.close()

    def __enter__(self) -> "SqliteVectorStore":
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
                    f"SELECT content_hash FROM {self._vectors} "  # noqa: S608
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
            self._mark_written()
            self._db.executemany(
                f"INSERT OR REPLACE INTO {self._vectors} "  # noqa: S608
                "(content_hash, model_id, dim, data) VALUES (?, ?, ?, ?)",
                rows,
            )

    def set_file(
        self, path: Path, stamp: FileStamp, chunks: Sequence[tuple[Chunk, str]]
    ) -> None:
        """Replace everything held for *path*."""
        text = str(path)
        with self._lock, self._db:
            self._mark_written()
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
            self._mark_written()
            self._db.executemany(
                "DELETE FROM files WHERE path = ?", [(str(p),) for p in paths]
            )

    def prune_vectors(self) -> int:
        """Delete vectors no chunk references. Return how many went.

        A shared store is referenced by every index of the repository,
        so ask each sibling what it still uses before anything goes. A
        sibling that is gone references nothing. A sibling that cannot
        be read keeps everything: absence has many causes, and a vector
        costs a second of embedding to make again.
        """
        with self._lock, self._db:
            kept: set[str] = set()
            for sibling in self._siblings:
                referenced = _referenced_by(sibling)
                if referenced is None:
                    log.warning("Cannot read %s. Sweeping nothing.", sibling)
                    return 0
                kept.update(referenced)
            self._mark_written()
            self._db.execute(
                "CREATE TEMP TABLE IF NOT EXISTS kept (content_hash TEXT PRIMARY KEY)"
            )
            self._db.execute("DELETE FROM kept")
            self._db.execute(
                "INSERT OR IGNORE INTO kept SELECT content_hash FROM main.chunks"
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO kept (content_hash) VALUES (?)",
                [(digest,) for digest in kept],
            )
            cursor = self._db.execute(
                f"DELETE FROM {self._vectors} WHERE content_hash NOT IN "  # noqa: S608
                "(SELECT content_hash FROM kept)"
            )
        return cursor.rowcount

    def _mark_written(self) -> None:
        """Note a write. Call it inside the write transaction.

        The kept matrix is stale once this store writes, and so is the
        matrix file beside the index. Count the write in ``meta`` as
        well, so a later process can tell. A replaced file can leave
        every row count unchanged while the vectors behind the ids
        change, so a count alone cannot tell.
        """
        self._writes += 1
        self._last_write = time.monotonic()
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES ('generation', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
        )

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

    def count(self) -> int:
        """Return how many chunks the store holds."""
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return int(row[0])

    def indexed_paths(self) -> Sequence[Path]:
        """Return every file the store has read, chunks or none."""
        with self._lock:
            rows = self._db.execute("SELECT path FROM files ORDER BY path").fetchall()
        return [Path(str(row[0])) for row in rows]

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
        keep: ResultFilter = None,
    ) -> Sequence[Match]:
        """Rank chunks by vector similarity, fused with a lexical order."""
        return rank(
            query_vector,
            query_text,
            limit,
            keep,
            semantic=self._semantic,
            lexical=self._lexical,
        )

    def _lexical(
        self, query_text: str, limit: int, keep: ResultFilter = None
    ) -> list[Chunk]:
        """Return the *limit* chunks *keep* allows, best BM25 match first."""
        match = _fts_query(query_text)
        if not match:
            return []

        kept: list[Chunk] = []
        try:
            with self._lock:
                cursor = self._db.execute(
                    "SELECT c.path, c.kind, c.language, c.symbol, "
                    "       c.start_line, c.end_line "
                    "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
                    "WHERE chunks_fts MATCH ? "
                    "ORDER BY bm25(chunks_fts, 2.0, 1.0)",
                    (match,),
                )
                # Read in pages and stop at the limit, so a filter that
                # rejects most rows still finds a full page behind them.
                while len(kept) < limit:
                    rows = cursor.fetchmany(max(limit, DETAIL_BATCH))
                    if not rows:
                        break
                    for row in rows:
                        chunk = self._to_chunk(row)
                        if keep is None or keep(chunk):
                            kept.append(chunk)
                            if len(kept) == limit:
                                break
        except sqlite3.OperationalError as exc:
            # A malformed match expression must not fail the whole query.
            log.debug("Lexical search skipped: %s", exc)
            return []

        return kept

    def _semantic(
        self, query_vector: Sequence[float], top: int, keep: ResultFilter = None
    ) -> list[Match]:
        """Return the *top* chunks *keep* allows, most similar first.

        Read only the vectors, score them as one matrix, then read the
        details of the few that won. Fetching every row's text and
        building a chunk for each cost far more than the arithmetic:
        measured on 7000 chunks, the multiplication took 1 ms while
        materializing every row took 60.
        """
        norm = math.sqrt(sum(value * value for value in query_vector))
        if norm == 0.0 or top <= 0:
            return []

        import numpy

        identifiers, matrix = self._scored_matrix(len(query_vector))
        if len(identifiers) == 0:
            return []

        query = numpy.asarray(query_vector, dtype=numpy.float32) / norm
        scores = matrix @ query

        if keep is None:
            count = min(top, len(identifiers))
            best = numpy.argpartition(-scores, count - 1)[:count]
            best = best[numpy.argsort(-scores[best], kind="stable")]
            return self._details(identifiers, scores, best)

        # Walk the whole order a slice at a time and stop once the filter
        # has let *top* through, so a narrow filter still fills its page.
        order = numpy.argsort(-scores, kind="stable")
        kept: list[Match] = []
        for start in range(0, len(order), max(top, DETAIL_BATCH)):
            for match in self._details(
                identifiers, scores, order[start : start + max(top, DETAIL_BATCH)]
            ):
                if keep(match.chunk):
                    kept.append(match)
                    if len(kept) == top:
                        return kept
        return kept

    def _scored_matrix(self, width: int):
        """Return the chunk ids and their vectors as one matrix.

        Build it once and keep it, because a query changes only the
        vector it is multiplied by. Rebuild when this store has written
        since, or when another connection has committed. Map the file
        beside the index when it describes the index as it is, and
        read the blobs out of SQLite otherwise.
        """
        import numpy

        with self._lock:
            token = (self._writes, self._data_version())
            if self._matrix_cache is not None and self._matrix_cache[0] == token:
                return self._matrix_cache[1], self._matrix_cache[2]

            stamp = self._stamp()
            mapped = self._map_matrix(width, stamp)
            if mapped is not None:
                self._matrix_cache = (token, *mapped)
                return mapped

            rows = self._vector_rows()
            if not rows:
                self._matrix_cache = (token, [], numpy.empty((0, width), "float32"))
                return self._matrix_cache[1], self._matrix_cache[2]

            wrong = next((dim for _i, dim, _d in rows if dim != width), None)
            if wrong is not None:
                raise ValueError(
                    f"The index holds {wrong}-dimension vectors but the query "
                    f"has {width}. Re-index with the current model."
                )

            identifiers, matrix = self._joined(rows, width)
            settled = time.monotonic() - self._last_write > SETTLE_SECONDS
            if self._writes == 0 or settled:
                self._save_matrix(stamp, identifiers, matrix)
            self._matrix_cache = (token, identifiers, matrix)
            return identifiers, matrix

    def _vector_rows(self) -> list[tuple[int, int, bytes]]:
        """Read the id, the width, and the vector of every chunk."""
        return self._db.execute(
            "SELECT c.id, v.dim, v.data "
            "FROM main.chunks c "
            f"JOIN {self._vectors} v ON v.content_hash = c.content_hash "  # noqa: S608
            "WHERE v.model_id = ?",
            (self._model_id,),
        ).fetchall()

    @staticmethod
    def _joined(rows: Sequence[tuple[int, int, bytes]], width: int):
        """Return the ids of *rows* and their vectors as one matrix."""
        import numpy

        matrix = numpy.frombuffer(
            b"".join(row[2] for row in rows), dtype=numpy.float32
        ).reshape(len(rows), width)
        return [int(row[0]) for row in rows], matrix

    def _data_version(self) -> int:
        """Return SQLite's counter of commits made by other connections."""
        return int(self._db.execute("PRAGMA data_version").fetchone()[0])

    def _stamp(self) -> tuple[int, int]:
        """Return the index state a matrix file must match to be current.

        The generation counts the writes made through this class. The
        row count guards against a writer that did not count, such as
        an older release still resident.
        """
        row = self._db.execute(
            "SELECT value FROM meta WHERE key = 'generation'"
        ).fetchone()
        generation = 0 if row is None else int(row[0])
        held = int(self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return generation, held

    def _map_matrix(self, width: int, stamp: tuple[int, int]):
        """Map the matrix file beside the index, or return None.

        Return None for a file that is absent, that describes another
        model, another width, or another state of the index, or that is
        not the size its header claims. The blobs are read then, and
        the file is written again.
        """
        import numpy

        path = self._matrix_path
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
            "model_id": self._model_id,
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
        self._saved = stamp
        return identifiers, matrix

    def _save_matrix(
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
                "model_id": self._model_id,
                "dim": int(matrix.shape[1]),
                "rows": int(matrix.shape[0]),
                "stamp": list(stamp),
            }
        ).encode("utf-8")
        if len(header) > MATRIX_HEADER_BYTES:
            return
        path = self._matrix_path
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
        self._saved = stamp

    def _save_matrix_if_current(self) -> None:
        """Leave the matrix file current when this store wrote.

        A store that wrote within the settle time kept the file back on
        each miss, and an index run never scores a matrix at all.
        Closing is when nothing waits on it. Read the vectors again when
        the kept matrix is stale or absent, so the first query of the
        next process maps the file and does not read the blobs.
        """
        if not self._writes:
            return
        stamp = self._stamp()
        if stamp == self._saved:
            return
        cache = self._matrix_cache
        if cache is not None and cache[0] == (self._writes, self._data_version()):
            self._save_matrix(stamp, cache[1], cache[2])
            return
        rows = self._vector_rows()
        if not rows:
            return
        width = rows[0][1]
        # A query names the width it reads, and refuses another. Here
        # nothing names one, so vectors of two widths leave no file.
        if any(dim != width for _i, dim, _d in rows):
            return
        self._save_matrix(stamp, *self._joined(rows, width))

    def _details(
        self, identifiers: Sequence[int], scores: Any, best: Any
    ) -> list[Match]:
        """Read the chunks behind the rows at positions *best*, in that order."""
        order = [int(identifiers[index]) for index in best]
        found = {int(identifiers[index]): float(scores[index]) for index in best}
        marks = ",".join("?" * len(order))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, path, kind, language, symbol, start_line, end_line "
                f"FROM chunks WHERE id IN ({marks})",  # noqa: S608
                order,
            ).fetchall()

        by_id = {int(row[0]): self._to_chunk(row[1:]) for row in rows}
        return [Match(by_id[key], found[key]) for key in order if key in by_id]
