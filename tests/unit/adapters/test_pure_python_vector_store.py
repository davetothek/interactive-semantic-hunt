"""Test the Pure Python vector store adapter."""

import pytest

from ish.adapters.vector_store.pure_python import (
    PurePythonVectorStore,
    cosine_similarity,
)
from ish.domain.chunk import Chunk


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


class TestPurePythonVectorStore:
    """Verify storing and searching behavior."""

    def _make_chunk(self, name: str) -> Chunk:
        """Helper to create dummy chunks for testing."""
        from pathlib import Path

        return Chunk(
            kind="function",
            symbol=name,
            path=Path("foo.py"),
            start_line=1,
            end_line=2,
            text="pass",
        )

    def test_add_mismatched_lengths(self) -> None:
        """Confirm it guards against bad input lengths."""
        store = PurePythonVectorStore()
        chunk = self._make_chunk("foo")

        with pytest.raises(ValueError, match="Got 1 chunks but 0 vectors"):
            store.add([chunk], [])

    def test_search_empty_store(self) -> None:
        """Confirm searching an empty store returns empty results."""
        store = PurePythonVectorStore()
        results = store.search([1.0, 0.0])
        assert results == []

    def test_search_ranking(self) -> None:
        """Confirm search returns chunks sorted by cosine similarity."""
        store = PurePythonVectorStore()

        c1 = self._make_chunk("perfect_match")
        c2 = self._make_chunk("orthogonal_match")
        c3 = self._make_chunk("opposite_match")

        # Add chunks with known vectors
        store.add(
            [c1, c2, c3],
            [
                [1.0, 0.0],  # Will be score 1.0 against [1.0, 0.0]
                [0.0, 1.0],  # Will be score 0.0
                [-1.0, 0.0],  # Will be score -1.0
            ],
        )

        results = store.search([1.0, 0.0])

        assert len(results) == 3
        # Should be ordered by score descending
        assert results[0][0].symbol == "perfect_match"
        assert results[0][1] == 1.0

        assert results[1][0].symbol == "orthogonal_match"
        assert results[1][1] == 0.0

        assert results[2][0].symbol == "opposite_match"
        assert results[2][1] == -1.0

    def test_search_limit(self) -> None:
        """Confirm search respects the limit argument."""
        store = PurePythonVectorStore()

        chunks = [self._make_chunk(f"f{i}") for i in range(10)]
        vectors = [[1.0, 0.0] for _ in range(10)]

        store.add(chunks, vectors)

        results = store.search([1.0, 0.0], limit=3)
        assert len(results) == 3
