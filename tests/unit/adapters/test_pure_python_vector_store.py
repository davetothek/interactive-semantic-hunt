"""Test the Pure Python vector store adapter."""

from pathlib import Path

import pytest

from ish.adapters.vector_store.pure_python import (
    PurePythonVectorStore,
    cosine_similarity,
)
from ish.application.ports.vector_store import FileStamp
from ish.domain.chunk import Chunk

STAMP = FileStamp(mtime_ns=1, size=1)


def make_chunk(name: str, path: str = "foo.py", line: int = 1) -> Chunk:
    """Build a dummy chunk for testing."""
    return Chunk(
        path=Path(path),
        text="pass",
        kind="function",
        language="python",
        symbol=name,
        start_line=line,
        end_line=line + 1,
    )


class TestCosineSimilarity:
    """Verify the pure math implementation of cosine similarity."""

    def test_identical_vectors(self) -> None:
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_orthogonal_vectors(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite_vectors(self) -> None:
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0

    def test_zero_vector(self) -> None:
        # A zero-magnitude vector should not raise ZeroDivisionError
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
        assert cosine_similarity([1.0, 0.0], [0.0, 0.0]) == 0.0

    def test_dimension_mismatch_raises(self) -> None:
        # A silent prefix comparison would return a wrong score.
        with pytest.raises(ValueError):
            cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0])


@pytest.fixture()
def store() -> PurePythonVectorStore:
    return PurePythonVectorStore()


class TestIndexMaintenance:
    """Verify the store tracks files, stamps, and vectors."""

    def test_starts_empty(self, store: PurePythonVectorStore) -> None:
        assert store.file_stamps() == {}
        assert store.chunks() == []

    def test_set_file_records_stamp_and_chunks(
        self, store: PurePythonVectorStore
    ) -> None:
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        assert store.file_stamps() == {Path("a.py"): STAMP}
        assert [c.symbol for c in store.chunks()] == ["f"]

    def test_set_file_replaces_previous_chunks(
        self, store: PurePythonVectorStore
    ) -> None:
        """Re-indexing a file must not leave the old definitions behind."""
        store.set_file(Path("a.py"), STAMP, [(make_chunk("old"), "h1")])
        store.set_file(Path("a.py"), STAMP, [(make_chunk("new"), "h2")])
        assert [c.symbol for c in store.chunks()] == ["new"]

    def test_missing_vectors_reports_absent_hashes(
        self, store: PurePythonVectorStore
    ) -> None:
        store.add_vectors({"h1": [1.0]})
        assert store.missing_vectors(["h1", "h2"]) == {"h2"}

    def test_missing_vectors_of_nothing(self, store: PurePythonVectorStore) -> None:
        assert store.missing_vectors([]) == set()

    def test_remove_files_drops_chunks(self, store: PurePythonVectorStore) -> None:
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        store.remove_files([Path("a.py")])
        assert store.file_stamps() == {}
        assert store.chunks() == []

    def test_clear_drops_files_but_keeps_vectors(
        self, store: PurePythonVectorStore
    ) -> None:
        store.add_vectors({"h1": [1.0, 0.0]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("f"), "h1")])
        store.clear()
        assert store.file_stamps() == {}
        assert store.missing_vectors(["h1"]) == set()

    def test_chunks_are_ordered(self, store: PurePythonVectorStore) -> None:
        store.set_file(
            Path("b.py"), STAMP, [(make_chunk("second", "b.py", 5), "h2")]
        )
        store.set_file(Path("a.py"), STAMP, [(make_chunk("first", "a.py", 1), "h1")])
        assert [c.symbol for c in store.chunks()] == ["first", "second"]

    def test_close_is_safe(self, store: PurePythonVectorStore) -> None:
        store.close()


class TestSearch:
    """Verify ranking behavior."""

    def test_search_empty_store(self, store: PurePythonVectorStore) -> None:
        assert store.search([1.0, 0.0]) == []

    def test_ranks_by_similarity(self, store: PurePythonVectorStore) -> None:
        store.add_vectors({"near": [1.0, 0.0], "far": [0.0, 1.0]})
        store.set_file(
            Path("a.py"),
            STAMP,
            [(make_chunk("near"), "near"), (make_chunk("far"), "far")],
        )
        results = store.search([1.0, 0.0], limit=2)
        assert [c.symbol for c, _ in results] == ["near", "far"]
        assert results[0][1] > results[1][1]

    def test_respects_limit(self, store: PurePythonVectorStore) -> None:
        store.add_vectors({f"h{i}": [float(i), 1.0] for i in range(5)})
        store.set_file(
            Path("a.py"),
            STAMP,
            [(make_chunk(f"s{i}"), f"h{i}") for i in range(5)],
        )
        assert len(store.search([1.0, 1.0], limit=2)) == 2

    def test_chunk_without_a_vector_is_skipped(
        self, store: PurePythonVectorStore
    ) -> None:
        """A chunk whose embedding failed must not break the search."""
        store.add_vectors({"has": [1.0, 0.0]})
        store.set_file(
            Path("a.py"),
            STAMP,
            [(make_chunk("has"), "has"), (make_chunk("none"), "absent")],
        )
        results = store.search([1.0, 0.0], limit=5)
        assert [c.symbol for c, _ in results] == ["has"]
