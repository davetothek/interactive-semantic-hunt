"""Test the shared vectors store of a repository.

A vector is keyed by the text it stands for, so two checkouts of one
repository can share every vector they agree on. The vectors of a
repository live in one store that every index of it attaches. Chunk
rows and file stamps stay in the index of each tree.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ish.adapters.vector_store.sqlite import (
    MIGRATES_FROM,
    SCHEMA_VERSION,
    SqliteVectorStore,
)
from ish.application.ports.vector_store import FileStamp
from ish.domain.chunk import Chunk

STAMP = FileStamp(mtime_ns=1, size=1)
MODEL = "model-a"


def make_chunk(name: str, path: str = "a.py", line: int = 1) -> Chunk:
    return Chunk(
        path=Path(path),
        text=f"def {name}(): pass",
        kind="function",
        language="python",
        symbol=name,
        start_line=line,
        end_line=line + 1,
    )


def tables_of(db_path: Path) -> set[str]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        db.close()
    return {str(row[0]) for row in rows}


def hashes_in(db_path: Path) -> set[str]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT content_hash FROM vectors").fetchall()
    finally:
        db.close()
    return {str(row[0]) for row in rows}


def meta_of(db_path: Path) -> dict[str, str]:
    db = sqlite3.connect(db_path)
    try:
        return dict(db.execute("SELECT key, value FROM meta").fetchall())
    finally:
        db.close()


@pytest.fixture()
def shared(tmp_path: Path) -> Path:
    return tmp_path / "idx" / "vectors-abc.db"


def index_at(
    tmp_path: Path, name: str, shared: Path | None, **extra
) -> SqliteVectorStore:
    return SqliteVectorStore(
        tmp_path / "idx" / f"{name}.db",
        model_id=MODEL,
        root=tmp_path / name,
        repository=tmp_path / ".git",
        checkout=tmp_path / name,
        shared=shared,
        **extra,
    )


class TestSharing:
    """Verify two indexes of one repository share their vectors."""

    def test_vectors_land_in_the_shared_store(
        self, tmp_path: Path, shared: Path
    ) -> None:
        store = index_at(tmp_path, "one", shared)
        store.add_vectors({"h1": [1.0, 0.0]})
        store.close()
        assert hashes_in(shared) == {"h1"}
        assert "vectors" not in tables_of(tmp_path / "idx" / "one.db")

    def test_a_sibling_finds_them(self, tmp_path: Path, shared: Path) -> None:
        """This is the whole point: the second checkout embeds nothing."""
        first = index_at(tmp_path, "one", shared)
        first.add_vectors({"h1": [1.0, 0.0], "h2": [0.0, 1.0]})
        first.close()

        second = index_at(tmp_path, "two", shared)
        try:
            assert second.missing_vectors({"h1", "h2", "h3"}) == {"h3"}
            second.set_file(Path("b.py"), STAMP, [(make_chunk("g", "b.py"), "h2")])
            assert second.search([0.0, 1.0], limit=1)[0].chunk.symbol == "g"
        finally:
            second.close()

    def test_the_shared_store_is_reached_again_unaided(
        self, tmp_path: Path, shared: Path
    ) -> None:
        """An index names its store, so a later opening without git still finds it."""
        store = index_at(tmp_path, "one", shared)
        store.add_vectors({"h1": [1.0, 0.0]})
        store.close()

        again = SqliteVectorStore(tmp_path / "idx" / "one.db", model_id=MODEL)
        try:
            assert again.missing_vectors({"h1"}) == set()
        finally:
            again.close()

    def test_the_shared_store_names_its_repository(
        self, tmp_path: Path, shared: Path
    ) -> None:
        index_at(tmp_path, "one", shared).close()
        assert meta_of(shared) == {
            "kind": "vectors",
            "repository": str(tmp_path / ".git"),
        }
        assert SqliteVectorStore.read_identity(shared) == {
            "repository": tmp_path / ".git"
        }

    def test_reopening_writes_nothing_to_the_shared_store(
        self, tmp_path: Path, shared: Path
    ) -> None:
        """A write on open would stall behind every worktree indexing, for nothing."""
        index_at(tmp_path, "one", shared).close()
        watcher = sqlite3.connect(shared)
        try:
            before = watcher.execute("PRAGMA data_version").fetchone()[0]
            for name in ("one", "two", "one"):
                index_at(tmp_path, name, shared).close()
            after = watcher.execute("PRAGMA data_version").fetchone()[0]
        finally:
            watcher.close()
        assert after == before

    def test_a_tree_outside_a_repository_keeps_its_own(self, tmp_path: Path) -> None:
        store = SqliteVectorStore(tmp_path / "idx" / "alone.db", model_id=MODEL)
        store.add_vectors({"h1": [1.0, 0.0]})
        store.close()
        assert hashes_in(tmp_path / "idx" / "alone.db") == {"h1"}

    def test_a_siblings_write_keeps_the_matrix(
        self, tmp_path: Path, shared: Path
    ) -> None:
        """A chunk row is written only after its vector exists, so what a
        sibling adds to the shared store changes nothing this index reads."""
        first = index_at(tmp_path, "one", shared)
        first.add_vectors({"h1": [1.0, 0.0]})
        first.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        matrix = first._scored_matrix(2)[1]

        sibling = index_at(tmp_path, "two", shared)
        sibling.add_vectors({"h2": [0.0, 1.0]})
        sibling.close()
        try:
            assert first._scored_matrix(2)[1] is matrix
            assert first.search([1.0, 0.0], limit=1)[0].chunk.symbol == "f"
        finally:
            first.close()

    def test_another_process_shares_the_vectors(
        self, tmp_path: Path, shared: Path
    ) -> None:
        """Two checkouts are indexed by two processes, not two threads."""
        index_at(tmp_path, "one", shared).close()
        script = (
            "from pathlib import Path\n"
            "from ish.adapters.vector_store.sqlite import SqliteVectorStore\n"
            f"store = SqliteVectorStore(Path({str(tmp_path / 'idx' / 'two.db')!r}), "
            f"model_id={MODEL!r}, shared=Path({str(shared)!r}))\n"
            "store.add_vectors({'h9': [1.0, 0.0]})\n"
            "store.close()\n"
        )
        subprocess.run([sys.executable, "-c", script], check=True, timeout=60)

        first = index_at(tmp_path, "one", shared)
        try:
            assert first.missing_vectors({"h9"}) == set()
        finally:
            first.close()


class TestMigration:
    """Verify an index from the version before keeps every vector."""

    def _older(self, tmp_path: Path) -> Path:
        """An index with local vectors, marked as the version before this one."""
        store = SqliteVectorStore(tmp_path / "idx" / "old.db", model_id=MODEL)
        store.add_vectors({"h1": [1.0, 0.0], "h2": [0.0, 1.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        store.close()
        db = sqlite3.connect(tmp_path / "idx" / "old.db")
        with db:
            db.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (MIGRATES_FROM,),
            )
        db.close()
        return tmp_path / "idx" / "old.db"

    def test_vectors_move_into_the_shared_store(
        self, tmp_path: Path, shared: Path
    ) -> None:
        old = self._older(tmp_path)
        store = SqliteVectorStore(old, model_id=MODEL, shared=shared)
        try:
            assert store.missing_vectors({"h1", "h2"}) == set()
            assert store.search([1.0, 0.0], limit=1)[0].chunk.symbol == "f"
        finally:
            store.close()
        assert hashes_in(shared) == {"h1", "h2"}
        assert "vectors" not in tables_of(old)
        assert meta_of(old)["schema_version"] == SCHEMA_VERSION
        assert meta_of(old)["shared"] == str(shared)

    def test_outside_a_repository_the_table_stays(self, tmp_path: Path) -> None:
        old = self._older(tmp_path)
        store = SqliteVectorStore(old, model_id=MODEL)
        try:
            assert store.missing_vectors({"h1", "h2"}) == set()
        finally:
            store.close()
        assert hashes_in(old) == {"h1", "h2"}
        assert meta_of(old)["schema_version"] == SCHEMA_VERSION

    def test_the_matrix_file_stays_current(self, tmp_path: Path, shared: Path) -> None:
        """The join reads the same vectors from another file. Nothing changed."""
        old = self._older(tmp_path)
        store = SqliteVectorStore(old, model_id=MODEL, shared=shared)
        try:
            assert store.search([1.0, 0.0], limit=1)[0].chunk.symbol == "f"
        finally:
            store.close()

    def test_a_rebuild_leaves_the_shared_store_alone(
        self, tmp_path: Path, shared: Path
    ) -> None:
        store = index_at(tmp_path, "one", shared)
        store.add_vectors({"h1": [1.0, 0.0]})
        store.close()
        db = sqlite3.connect(tmp_path / "idx" / "one.db")
        with db:
            db.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
        db.close()

        again = index_at(tmp_path, "one", shared)
        try:
            assert again.count() == 0
            assert again.missing_vectors({"h1"}) == set()
        finally:
            again.close()
        assert hashes_in(shared) == {"h1"}


class TestSweeping:
    """Verify a sweep of the shared store spares what a sibling uses."""

    def _two(self, tmp_path: Path, shared: Path, **extra) -> SqliteVectorStore:
        other = index_at(tmp_path, "two", shared)
        other.add_vectors({"h2": [0.0, 1.0], "h3": [1.0, 1.0]})
        other.set_file(Path("b.py"), STAMP, [(make_chunk("g", "b.py"), "h2")])
        other.close()
        store = index_at(tmp_path, "one", shared, **extra)
        store.add_vectors({"h1": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        return store

    def test_what_a_sibling_uses_survives(self, tmp_path: Path, shared: Path) -> None:
        store = self._two(tmp_path, shared, siblings=[tmp_path / "idx" / "two.db"])
        try:
            assert store.prune_vectors() == 1
        finally:
            store.close()
        assert hashes_in(shared) == {"h1", "h2"}

    def test_a_sibling_that_is_gone_references_nothing(
        self, tmp_path: Path, shared: Path
    ) -> None:
        store = self._two(tmp_path, shared, siblings=[tmp_path / "idx" / "gone.db"])
        try:
            assert store.prune_vectors() == 2
        finally:
            store.close()
        assert hashes_in(shared) == {"h1"}

    def test_a_sibling_that_cannot_be_read_keeps_everything(
        self, tmp_path: Path, shared: Path
    ) -> None:
        junk = tmp_path / "idx" / "junk.db"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("not a database")
        store = self._two(tmp_path, shared, siblings=[junk])
        try:
            assert store.prune_vectors() == 0
        finally:
            store.close()
        assert hashes_in(shared) == {"h1", "h2", "h3"}

    def test_a_sibling_that_cannot_be_opened_keeps_everything(
        self, tmp_path: Path, shared: Path
    ) -> None:
        """A directory where an index should be is there, and unreadable."""
        blocking = tmp_path / "idx" / "dir.db"
        blocking.mkdir(parents=True, exist_ok=True)
        store = self._two(tmp_path, shared, siblings=[blocking])
        try:
            assert store.prune_vectors() == 0
        finally:
            store.close()
        assert hashes_in(shared) == {"h1", "h2", "h3"}

    def test_a_tree_of_its_own_sweeps_as_before(self, tmp_path: Path) -> None:
        store = SqliteVectorStore(tmp_path / "idx" / "alone.db", model_id=MODEL)
        store.add_vectors({"h1": [1.0, 0.0], "h2": [0.0, 1.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        try:
            assert store.prune_vectors() == 1
        finally:
            store.close()
