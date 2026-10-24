"""Test the matrix file the SQLite store keeps beside an index.

Reading every vector out of SQLite cost a process 310 to 460 ms on
224 MB of vectors. Mapping the same bytes from a file beside the index
cost 18 ms. The file is a cache: SQLite stays the source of truth, and
a file that does not describe the index as it is must be read past.
"""

import json
import os
import sqlite3
from pathlib import Path

import numpy
import pytest

from ish.adapters.vector_store import sqlite as module
from ish.adapters.vector_store.sqlite import (
    MATRIX_HEADER_BYTES,
    MATRIX_SUFFIX,
    SqliteVectorStore,
)
from ish.application.ports.vector_store import FileStamp
from ish.domain.chunk import Chunk

STAMP = FileStamp(mtime_ns=1, size=1)
TOWARD_F = [1.0, 0.0]
TOWARD_G = [0.0, 1.0]


def make_chunk(name: str, line: int = 1) -> Chunk:
    return Chunk(
        path=Path("a.py"),
        text="",
        kind="function",
        language="python",
        symbol=name,
        start_line=line,
        end_line=line + 1,
    )


def filled(db_path: Path, *, model: str = "model-a") -> SqliteVectorStore:
    """Open a store holding two chunks, f toward x and g toward y."""
    store = SqliteVectorStore(db_path, model_id=model)
    store.add_vectors({"h1": TOWARD_F, "h2": TOWARD_G})
    store.set_file(
        Path("a.py"),
        STAMP,
        [(make_chunk("f"), "h1"), (make_chunk("g", line=5), "h2")],
    )
    return store


def best(store: SqliteVectorStore, toward: list[float]) -> str | None:
    return store.search(toward, limit=1)[0].chunk.symbol


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "index" / "test.db"


@pytest.fixture()
def matrix_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + MATRIX_SUFFIX)


@pytest.fixture()
def saved(db_path: Path, matrix_path: Path) -> Path:
    """A closed store that left a current matrix file behind."""
    store = filled(db_path)
    store.search(TOWARD_F, limit=1)
    store.close()
    assert matrix_path.exists()
    return matrix_path


def rewrite_header(matrix_path: Path, **changes: object) -> None:
    """Change named fields of the header, leaving the body as it is."""
    body = matrix_path.read_bytes()
    header = json.loads(body[:MATRIX_HEADER_BYTES])
    header.update(changes)
    padded = json.dumps(header).encode("utf-8").ljust(MATRIX_HEADER_BYTES)
    matrix_path.write_bytes(padded + body[MATRIX_HEADER_BYTES:])


class TestWriting:
    """Verify when the file is written."""

    def test_a_store_that_wrote_waits_for_close(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        """An index run commits often, so a writer holds the file back."""
        store = filled(db_path)
        store.search(TOWARD_F, limit=1)
        assert not matrix_path.exists()
        store.close()
        assert matrix_path.exists()

    def test_a_settled_writer_writes_on_a_miss(
        self, db_path: Path, matrix_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(module, "SETTLE_SECONDS", -1.0)
        store = filled(db_path)
        try:
            store.search(TOWARD_F, limit=1)
            assert matrix_path.exists()
        finally:
            store.close()

    def test_a_reader_writes_on_a_miss(self, saved: Path, db_path: Path) -> None:
        saved.unlink()
        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            reader.search(TOWARD_F, limit=1)
            assert saved.exists()
        finally:
            reader.close()

    def test_an_empty_index_leaves_no_file(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        store = SqliteVectorStore(db_path, model_id="model-a")
        store.search(TOWARD_F, limit=1)
        store.close()
        assert not matrix_path.exists()

    def test_a_stale_matrix_is_read_again_before_close_writes_it(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        """Another process committed, so what this store holds is old."""
        first = filled(db_path)
        first.search(TOWARD_F, limit=1)
        other = SqliteVectorStore(db_path, model_id="model-a")
        other.set_file(Path("b.py"), STAMP, [(make_chunk("h", line=9), "h1")])
        first.close()
        other.close()

        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            identifiers, _matrix = reader._map_matrix(2, reader._stamp())
            assert len(identifiers) == 3
        finally:
            reader.close()

    def test_a_file_that_cannot_be_written_is_skipped(
        self, db_path: Path, matrix_path: Path, monkeypatch
    ) -> None:
        def refuse(source, target):
            raise OSError("read-only")

        monkeypatch.setattr(module.os, "replace", refuse)
        store = filled(db_path)
        store.search(TOWARD_F, limit=1)
        store.close()
        assert not matrix_path.exists()
        assert list(matrix_path.parent.glob("*.partial")) == []

    def test_a_header_too_long_to_write_is_skipped(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        store = filled(db_path, model="m" * (MATRIX_HEADER_BYTES + 1))
        store.search(TOWARD_F, limit=1)
        store.close()
        assert not matrix_path.exists()

    def test_closing_twice_is_safe(self, db_path: Path) -> None:
        store = filled(db_path)
        store.search(TOWARD_F, limit=1)
        store.close()
        store.close()


class TestAnIndexRunLeavesTheMatrixFile:
    """An index run writes and never scores, and still leaves the file.

    Without it, the first query after an index run read every vector
    and wrote the file on the keystroke path: 7,505 ms on 49,740
    chunks, against 6,063 ms with the file in place.
    """

    def test_a_store_that_only_wrote_writes_the_file_on_close(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        filled(db_path).close()
        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            mapped = reader._map_matrix(2, reader._stamp())
            assert mapped is not None
            assert best(reader, TOWARD_G) == "g"
        finally:
            reader.close()

    def test_the_file_matches_the_one_a_query_writes(
        self, tmp_path: Path, db_path: Path, matrix_path: Path
    ) -> None:
        filled(db_path).close()
        written_at_close = matrix_path.read_bytes()

        matrix_path.unlink()
        reader = SqliteVectorStore(db_path, model_id="model-a")
        reader.search(TOWARD_F, limit=1)
        reader.close()
        assert matrix_path.read_bytes() == written_at_close

    def test_a_store_that_did_not_write_leaves_nothing(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        filled(db_path).close()
        matrix_path.unlink()
        SqliteVectorStore(db_path, model_id="model-a").close()
        assert not matrix_path.exists()

    def test_vectors_of_two_widths_leave_no_file(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        """A query names the width it needs. A close has none to name."""
        store = SqliteVectorStore(db_path, model_id="model-a")
        store.add_vectors({"h1": TOWARD_F, "h2": [1.0, 0.0, 0.0]})
        store.set_file(
            Path("a.py"),
            STAMP,
            [(make_chunk("f"), "h1"), (make_chunk("g", line=5), "h2")],
        )
        store.close()
        assert not matrix_path.exists()

    def test_a_store_whose_vectors_are_all_gone_leaves_no_file(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        store = SqliteVectorStore(db_path, model_id="model-a")
        store.set_file(Path("a.py"), STAMP, [])
        store.close()
        assert not matrix_path.exists()


class TestReading:
    """Verify a later process maps the file and answers from it."""

    def test_a_later_process_maps_the_file(self, saved: Path, db_path: Path) -> None:
        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            identifiers, matrix = reader._scored_matrix(2)
            assert isinstance(matrix, numpy.memmap)
            assert isinstance(identifiers, numpy.memmap)
            assert best(reader, TOWARD_F) == "f"
            assert best(reader, TOWARD_G) == "g"
        finally:
            reader.close()

    def test_a_mapped_answer_matches_the_stored_one(
        self, db_path: Path, matrix_path: Path
    ) -> None:
        store = filled(db_path)
        from_blobs = store.search([0.6, 0.8], limit=2)
        store.close()

        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            from_file = reader.search([0.6, 0.8], limit=2)
        finally:
            reader.close()
        assert [(m.chunk.symbol, round(m.score, 6)) for m in from_file] == [
            (m.chunk.symbol, round(m.score, 6)) for m in from_blobs
        ]

    def test_the_mapped_matrix_is_kept_between_queries(
        self, saved: Path, db_path: Path
    ) -> None:
        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            first = reader._scored_matrix(2)[1]
            second = reader._scored_matrix(2)[1]
            assert first is second
        finally:
            reader.close()


class TestStaleness:
    """Verify a file that no longer describes the index is read past."""

    def test_a_write_with_equal_counts_is_noticed(
        self, saved: Path, db_path: Path
    ) -> None:
        """Swap the vectors behind two chunks. Every count stays the same."""
        writer = SqliteVectorStore(db_path, model_id="model-a")
        try:
            writer.set_file(
                Path("a.py"),
                STAMP,
                [(make_chunk("f"), "h2"), (make_chunk("g", line=5), "h1")],
            )
            assert best(writer, TOWARD_F) == "g"
            assert not isinstance(writer._scored_matrix(2)[1], numpy.memmap)
        finally:
            writer.close()

    def test_every_write_moves_the_generation(self, db_path: Path) -> None:
        store = SqliteVectorStore(db_path, model_id="model-a")
        watcher = sqlite3.connect(db_path)

        def generation() -> int:
            row = watcher.execute(
                "SELECT value FROM meta WHERE key = 'generation'"
            ).fetchone()
            return 0 if row is None else int(row[0])

        try:
            assert generation() == 0
            store.add_vectors({"h1": TOWARD_F})
            assert generation() == 1
            store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
            assert generation() == 2
            store.remove_files([Path("a.py")])
            assert generation() == 3
            store.clear()
            assert generation() == 4
            store.prune_vectors()
            assert generation() == 5
        finally:
            watcher.close()
            store.close()

    def test_another_model_reads_its_own_blobs(
        self, saved: Path, db_path: Path
    ) -> None:
        other = SqliteVectorStore(db_path, model_id="model-b")
        try:
            identifiers, _matrix = other._scored_matrix(2)
            assert len(identifiers) == 0
        finally:
            other.close()
        header = json.loads(saved.read_bytes()[:MATRIX_HEADER_BYTES])
        assert header["model_id"] == "model-a"

    def test_another_width_reads_the_blobs(self, saved: Path, db_path: Path) -> None:
        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            with pytest.raises(ValueError, match="2-dimension"):
                reader._scored_matrix(3)
        finally:
            reader.close()


class TestDamage:
    """Verify a damaged file is read past, and written whole again."""

    def _answers_from_blobs(
        self, db_path: Path, matrix_path: Path, mapped: type = numpy.memmap
    ) -> None:
        """Assert the answer came from the blobs, and the file was written whole.

        Take the mapped type as a default, bound when the module loads,
        so a test that replaces ``numpy.memmap`` can still name it.
        """
        reader = SqliteVectorStore(db_path, model_id="model-a")
        try:
            assert best(reader, TOWARD_G) == "g"
            assert not isinstance(reader._scored_matrix(2)[1], mapped)
        finally:
            reader.close()
        header = json.loads(matrix_path.read_bytes()[:MATRIX_HEADER_BYTES])
        assert header["rows"] == 2

    def test_a_truncated_file(self, saved: Path, db_path: Path) -> None:
        with saved.open("r+b") as handle:
            handle.truncate(MATRIX_HEADER_BYTES + 8)
        self._answers_from_blobs(db_path, saved)

    def test_a_file_of_garbage(self, saved: Path, db_path: Path) -> None:
        saved.write_bytes(b"not a header" * 500)
        self._answers_from_blobs(db_path, saved)

    def test_a_header_that_is_not_an_object(self, saved: Path, db_path: Path) -> None:
        body = saved.read_bytes()
        saved.write_bytes(
            b"[1, 2]".ljust(MATRIX_HEADER_BYTES) + body[MATRIX_HEADER_BYTES:]
        )
        self._answers_from_blobs(db_path, saved)

    def test_a_row_count_that_is_not_a_number(self, saved: Path, db_path: Path) -> None:
        rewrite_header(saved, rows="two")
        self._answers_from_blobs(db_path, saved)

    def test_a_file_that_cannot_be_mapped(
        self, saved: Path, db_path: Path, monkeypatch
    ) -> None:
        def refuse(*args, **kwargs):
            raise ValueError("no mapping")

        monkeypatch.setattr(numpy, "memmap", refuse)
        self._answers_from_blobs(db_path, saved)

    def test_the_source_is_not_in_the_file(self, saved: Path) -> None:
        """The file holds ids and vectors, never a path or a symbol."""
        body = saved.read_bytes()
        assert b"a.py" not in body
        assert b"def " not in body


class TestNaming:
    def test_the_file_sits_beside_the_index(self, saved: Path, db_path: Path) -> None:
        assert saved.parent == db_path.parent
        assert saved.name == f"{db_path.name}{MATRIX_SUFFIX}"

    def test_the_catalog_does_not_mistake_it_for_an_index(self, saved: Path) -> None:
        assert not saved.match("*.db")

    def test_partial_files_are_named_after_the_process(
        self, db_path: Path, matrix_path: Path, monkeypatch
    ) -> None:
        seen: list[str] = []
        original = os.replace

        def watch(source, target):
            seen.append(Path(source).name)
            original(source, target)

        monkeypatch.setattr(module.os, "replace", watch)
        store = filled(db_path)
        store.search(TOWARD_F, limit=1)
        store.close()
        assert seen == [f"{matrix_path.name}.{os.getpid()}.partial"]
