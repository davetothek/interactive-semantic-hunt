"""Test the SQLite vector store adapter."""

import math
import sqlite3
from pathlib import Path

import pytest

from ish.adapters.vector_store.sqlite import SCHEMA_VERSION, SqliteVectorStore
from ish.application.ports.vector_store import FileStamp
from ish.domain.chunk import Chunk

STAMP = FileStamp(mtime_ns=1, size=1)


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


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "index" / "test.db"


@pytest.fixture()
def store(db_path: Path):
    store = SqliteVectorStore(db_path, model_id="model-a")
    yield store
    store.close()


class TestPersistence:
    """Verify that a later process sees the earlier one's work."""

    def test_creates_the_parent_directory(self, db_path: Path) -> None:
        SqliteVectorStore(db_path, model_id="m").close()
        assert db_path.exists()

    def test_survives_reopening(self, db_path: Path) -> None:
        first = SqliteVectorStore(db_path, model_id="model-a")
        first.add_vectors({"h1": [1.0, 0.0]})
        first.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        first.close()

        second = SqliteVectorStore(db_path, model_id="model-a")
        try:
            assert second.file_stamps() == {Path("a.py"): STAMP}
            assert [c.symbol for c in second.chunks()] == ["f"]
            assert second.missing_vectors(["h1"]) == set()
        finally:
            second.close()

    def test_context_manager_closes(self, db_path: Path) -> None:
        with SqliteVectorStore(db_path, model_id="m") as store:
            store.add_vectors({"h": [1.0]})
        with pytest.raises(sqlite3.ProgrammingError):
            store.file_stamps()


class TestModelIsolation:
    """Verify that one model never reads another model's vectors."""

    def test_other_model_vectors_are_missing(self, db_path: Path) -> None:
        a = SqliteVectorStore(db_path, model_id="model-a")
        a.add_vectors({"h1": [1.0, 0.0]})
        a.close()

        b = SqliteVectorStore(db_path, model_id="model-b")
        try:
            assert b.missing_vectors(["h1"]) == {"h1"}
        finally:
            b.close()

    def test_switching_back_reuses_the_original(self, db_path: Path) -> None:
        a = SqliteVectorStore(db_path, model_id="model-a")
        a.add_vectors({"h1": [1.0, 0.0]})
        a.close()

        b = SqliteVectorStore(db_path, model_id="model-b")
        b.add_vectors({"h1": [0.0, 1.0]})
        b.close()

        again = SqliteVectorStore(db_path, model_id="model-a")
        try:
            assert again.missing_vectors(["h1"]) == set()
        finally:
            again.close()

    def test_search_ignores_the_other_model(self, db_path: Path) -> None:
        a = SqliteVectorStore(db_path, model_id="model-a")
        a.add_vectors({"h1": [1.0, 0.0]})
        a.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        a.close()

        b = SqliteVectorStore(db_path, model_id="model-b")
        try:
            assert b.search([1.0, 0.0]) == []
        finally:
            b.close()


class TestSearch:
    """Verify ranking and the normalization the store applies on write."""

    def test_ranks_by_similarity(self, store: SqliteVectorStore) -> None:
        store.add_vectors({"near": [1.0, 0.0], "far": [0.0, 1.0]})
        store.set_file(
            Path("a.py"),
            STAMP,
            [(make_chunk("near"), "near"), (make_chunk("far", line=3), "far")],
        )
        results = store.search([1.0, 0.0], limit=2)
        assert [c.symbol for c, _ in results] == ["near", "far"]

    def test_score_matches_cosine(self, store: SqliteVectorStore) -> None:
        """Storing at unit length must not change the reported score."""
        store.add_vectors({"h": [3.0, 4.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h")])

        (_chunk, score), = store.search([1.0, 0.0], limit=1)
        assert score == pytest.approx(3.0 / 5.0, abs=1e-6)

    def test_magnitude_does_not_change_ranking(self, store: SqliteVectorStore) -> None:
        store.add_vectors({"big": [100.0, 0.0], "small": [0.1, 0.05]})
        store.set_file(
            Path("a.py"),
            STAMP,
            [(make_chunk("big"), "big"), (make_chunk("small", line=3), "small")],
        )
        results = store.search([1.0, 0.0], limit=2)
        assert [c.symbol for c, _ in results] == ["big", "small"]

    def test_zero_query_returns_nothing(self, store: SqliteVectorStore) -> None:
        store.add_vectors({"h": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h")])
        assert store.search([0.0, 0.0]) == []

    def test_zero_vector_is_stored_safely(self, store: SqliteVectorStore) -> None:
        store.add_vectors({"h": [0.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h")])
        (_chunk, score), = store.search([1.0, 0.0], limit=1)
        assert score == 0.0

    def test_dimension_mismatch_is_reported(self, store: SqliteVectorStore) -> None:
        store.add_vectors({"h": [1.0, 0.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h")])
        with pytest.raises(ValueError, match="Re-index"):
            store.search([1.0, 0.0])

    def test_empty_store_returns_nothing(self, store: SqliteVectorStore) -> None:
        assert store.search([1.0, 0.0]) == []

    def test_chunk_round_trips(self, store: SqliteVectorStore) -> None:
        original = make_chunk("f")
        store.add_vectors({"h": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(original, "h")])
        (restored, _score), = store.search([1.0, 0.0], limit=1)
        assert restored == original

    def test_null_symbol_round_trips(self, store: SqliteVectorStore) -> None:
        anonymous = Chunk(
            path=Path("a.py"),
            text="x = 1",
            kind="module",
            language="python",
            symbol=None,
            start_line=1,
            end_line=1,
        )
        store.add_vectors({"h": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(anonymous, "h")])
        assert store.chunks()[0].symbol is None


class TestMaintenance:
    """Verify replacement, removal, and vector pruning."""

    def test_set_file_replaces_chunks(self, store: SqliteVectorStore) -> None:
        store.set_file(Path("a.py"), STAMP, [(make_chunk("old"), "h1")])
        store.set_file(Path("a.py"), STAMP, [(make_chunk("new"), "h2")])
        assert [c.symbol for c in store.chunks()] == ["new"]

    def test_remove_files_cascades_to_chunks(self, store: SqliteVectorStore) -> None:
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        store.remove_files([Path("a.py")])
        assert store.file_stamps() == {}
        assert store.chunks() == []

    def test_remove_nothing_is_safe(self, store: SqliteVectorStore) -> None:
        store.remove_files([])

    def test_add_nothing_is_safe(self, store: SqliteVectorStore) -> None:
        store.add_vectors({})

    def test_remove_keeps_vectors_for_reuse(self, store: SqliteVectorStore) -> None:
        """Restoring a deleted file must not cost an embedding."""
        store.add_vectors({"h1": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        store.remove_files([Path("a.py")])
        assert store.missing_vectors(["h1"]) == set()

    def test_clear_drops_files_but_keeps_vectors(
        self, store: SqliteVectorStore
    ) -> None:
        store.add_vectors({"h1": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        store.clear()
        assert store.file_stamps() == {}
        assert store.chunks() == []
        assert store.missing_vectors(["h1"]) == set()

    def test_prune_removes_unreferenced_vectors(
        self, store: SqliteVectorStore
    ) -> None:
        store.add_vectors({"kept": [1.0, 0.0], "orphan": [0.0, 1.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "kept")])

        assert store.prune_vectors() == 1
        assert store.missing_vectors(["orphan"]) == {"orphan"}
        assert store.missing_vectors(["kept"]) == set()

    def test_missing_vectors_batches_large_input(
        self, store: SqliteVectorStore
    ) -> None:
        """Stay inside the SQLite variable limit for a big index."""
        hashes = [f"h{i}" for i in range(1200)]
        store.add_vectors({h: [1.0, 0.0] for h in hashes[:600]})
        assert store.missing_vectors(hashes) == set(hashes[600:])


class TestSchemaVersion:
    """Verify that an index from an older build is rebuilt, not misread."""

    def test_stale_schema_is_rebuilt(self, db_path: Path) -> None:
        store = SqliteVectorStore(db_path, model_id="m")
        store.add_vectors({"h": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h")])
        store.close()

        db = sqlite3.connect(db_path)
        db.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
        db.commit()
        db.close()

        rebuilt = SqliteVectorStore(db_path, model_id="m")
        try:
            assert rebuilt.file_stamps() == {}
            assert rebuilt.missing_vectors(["h"]) == {"h"}
        finally:
            rebuilt.close()

    def test_current_schema_is_kept(self, db_path: Path) -> None:
        store = SqliteVectorStore(db_path, model_id="m")
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h")])
        store.close()

        again = SqliteVectorStore(db_path, model_id="m")
        try:
            assert again.file_stamps() == {Path("a.py"): STAMP}
        finally:
            again.close()

    def test_version_is_recorded(self, db_path: Path) -> None:
        SqliteVectorStore(db_path, model_id="m").close()
        db = sqlite3.connect(db_path)
        row = db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        db.close()
        assert row[0] == SCHEMA_VERSION


class TestConcurrentReaders:
    """Verify that MCP, nvim, and the CLI can query the same index at once."""

    def test_second_connection_reads_while_first_is_open(
        self, db_path: Path
    ) -> None:
        writer = SqliteVectorStore(db_path, model_id="m")
        writer.add_vectors({"h": [1.0, 0.0]})
        writer.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h")])

        reader = SqliteVectorStore(db_path, model_id="m")
        try:
            assert [c.symbol for c in reader.chunks()] == ["f"]
            assert len(reader.search([1.0, 0.0], limit=1)) == 1
        finally:
            reader.close()
            writer.close()

    def test_journal_mode_is_wal(self, db_path: Path) -> None:
        store = SqliteVectorStore(db_path, model_id="m")
        try:
            mode = store._db.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            store.close()
        assert mode.lower() == "wal"


class TestPacking:
    """Verify the float32 encoding preserves direction."""

    def test_round_trip_preserves_unit_direction(self) -> None:
        from ish.adapters.vector_store.sqlite import _pack, _unpack

        values = _unpack(_pack([3.0, 4.0]))
        assert math.isclose(values[0], 0.6, abs_tol=1e-6)
        assert math.isclose(values[1], 0.8, abs_tol=1e-6)


def test_missing_vectors_of_nothing(store: SqliteVectorStore) -> None:
    assert store.missing_vectors([]) == set()
