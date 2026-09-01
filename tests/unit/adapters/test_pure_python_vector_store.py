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
        store.set_file(Path("b.py"), STAMP, [(make_chunk("second", "b.py", 5), "h2")])
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


class TestHybridSearch:
    """Verify the in-memory store offers the same hybrid contract."""

    def _seed(self, store: PurePythonVectorStore) -> None:
        """All vectors identical, so only the lexical half can order them."""
        names = ["prune_vectors", "load_config", "cosine_similarity"]
        store.add_vectors({n: [0.0, 1.0] for n in names})
        store.set_file(
            Path("a.py"),
            STAMP,
            [
                (
                    Chunk(
                        path=Path("a.py"),
                        text=f"def {n}(): pass",
                        kind="function",
                        language="python",
                        symbol=n,
                        start_line=i,
                        end_line=i,
                    ),
                    n,
                )
                for i, n in enumerate(names, 1)
            ],
        )

    def test_identifier_query_finds_its_symbol(
        self, store: PurePythonVectorStore
    ) -> None:
        self._seed(store)
        results = store.search([0.0, 1.0], "prune_vectors", limit=1)
        assert results[0][0].symbol == "prune_vectors"

    def test_prose_query_keeps_the_vector_order(
        self, store: PurePythonVectorStore
    ) -> None:
        self._seed(store)
        plain = store.search([0.0, 1.0], "", limit=3)
        prose = store.search([0.0, 1.0], "read a settings file", limit=3)
        assert [c.symbol for c, _ in prose] == [c.symbol for c, _ in plain]

    def test_word_inside_a_name_matches(self, store: PurePythonVectorStore) -> None:
        self._seed(store)
        assert any(c.symbol == "cosine_similarity" for c in store._lexical("cosine", 5))

    def test_no_lexical_match_falls_back(self, store: PurePythonVectorStore) -> None:
        self._seed(store)
        results = store.search([0.0, 1.0], "absent_identifier", limit=3)
        assert len(results) == 3

    def test_lexical_of_nothing(self, store: PurePythonVectorStore) -> None:
        assert store._lexical("", limit=5) == []


class TestResultFilter:
    """Verify the keep predicate on the in-memory store."""

    def _seed(self, store: PurePythonVectorStore) -> None:
        store.add_vectors({"a": [1.0, 0.0], "b": [0.9, 0.1]})
        store.set_file(Path("a.py"), STAMP, [(make_chunk("alpha"), "a")])
        store.set_file(
            Path("b.md"),
            STAMP,
            [
                (
                    Chunk(
                        path=Path("b.md"),
                        text="t",
                        kind="section",
                        language="markdown",
                        symbol="Beta",
                        start_line=1,
                        end_line=1,
                    ),
                    "b",
                )
            ],
        )

    def test_filter_removes_a_language(self, store: PurePythonVectorStore) -> None:
        self._seed(store)
        results = store.search(
            [1.0, 0.0], limit=5, keep=lambda c: c.language == "markdown"
        )
        assert [c.symbol for c, _ in results] == ["Beta"]

    def test_filter_applies_to_the_lexical_half(
        self, store: PurePythonVectorStore
    ) -> None:
        self._seed(store)
        results = store.search(
            [1.0, 0.0], "alpha_thing", limit=5, keep=lambda c: c.language == "markdown"
        )
        assert all(c.language == "markdown" for c, _ in results)
