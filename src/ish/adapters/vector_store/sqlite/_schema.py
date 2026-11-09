"""The tables of an index and of the shared vector store of a repository."""

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

SCHEMA = """
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
VECTORS_SCHEMA = """
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
SHARED_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared.meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO shared.meta (key, value) VALUES ('kind', 'vectors');
"""
