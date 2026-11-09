"""SQLite: the persistent vector store, its tables, and its matrix file.

``store.py`` holds the store. ``_schema.py`` holds the tables and their
version, and ``_matrix.py`` the file of vectors mapped beside an index.
"""

from ish.adapters.vector_store.sqlite._matrix import MATRIX_HEADER_BYTES, MATRIX_SUFFIX
from ish.adapters.vector_store.sqlite._schema import (
    MIGRATES_FROM,
    SCHEMA_VERSION,
    SHARED_PREFIX,
)
from ish.adapters.vector_store.sqlite.store import SqliteVectorStore

__all__ = [
    "MATRIX_HEADER_BYTES",
    "MATRIX_SUFFIX",
    "MIGRATES_FROM",
    "SCHEMA_VERSION",
    "SHARED_PREFIX",
    "SqliteVectorStore",
]
