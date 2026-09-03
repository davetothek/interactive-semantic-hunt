"""Test the ranking helpers shared by the vector store adapters."""

from pathlib import Path

import pytest

from ish.application.ports.vector_store import (
    LEXICAL_WEIGHT,
    SEMANTIC_WEIGHT,
    fuse_rankings,
    is_code_like,
    split_identifier,
)
from ish.domain.chunk import Chunk


def chunk(symbol: str) -> Chunk:
    return Chunk(
        path=Path("a.py"),
        text="pass",
        kind="function",
        language="python",
        symbol=symbol,
        start_line=1,
        end_line=1,
    )


class TestSplitIdentifier:
    """Verify that a name becomes the words it is built from."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("cosine_similarity", "cosine similarity"),
            ("PythonParser.parse", "Python Parser parse"),
            ("SCHEMA_VERSION", "SCHEMA VERSION"),
            ("HTTPServer", "HTTP Server"),
            ("IshApp._update_preview", "Ish App update preview"),
            ("plain", "plain"),
            ("", ""),
        ],
    )
    def test_splits(self, name: str, expected: str) -> None:
        assert split_identifier(name) == expected


class TestIsCodeLike:
    """Verify the gate that decides whether lexical matching runs.

    Fusing a lexical ranking into a plain description costs accuracy,
    so the gate must stay closed for prose.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "prune_vectors",
            "SCHEMA_VERSION",
            "IshApp action_move",
            "camelCase",
            "find the HTTP handler",
        ],
    )
    def test_names_are_code_like(self, query: str) -> None:
        assert is_code_like(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "compute similarity between two vectors",
            "read a toml configuration file",
            "how does the index decide what changed",
            "",
        ],
    )
    def test_prose_is_not(self, query: str) -> None:
        assert is_code_like(query) is False

    def test_short_acronym_is_not_enough(self) -> None:
        """Two capitals is ordinary prose, not a name."""
        assert is_code_like("DO the thing") is False


class TestFuseRankings:
    """Verify weighted Reciprocal Rank Fusion."""

    def test_agreement_wins(self) -> None:
        a, b, c = chunk("a"), chunk("b"), chunk("c")
        fused = fuse_rankings([([b, a, c], 1.0), ([b, c, a], 1.0)], limit=3)
        assert fused[0] == b

    def test_weight_favours_the_stronger_list(self) -> None:
        """The semantic list must outrank the lexical one on a tie."""
        a, b = chunk("a"), chunk("b")
        fused = fuse_rankings(
            [([a, b], SEMANTIC_WEIGHT), ([b, a], LEXICAL_WEIGHT)], limit=2
        )
        assert fused[0] == a

    def test_equal_weights_make_it_a_tie_broken_by_order(self) -> None:
        a, b = chunk("a"), chunk("b")
        fused = fuse_rankings([([a, b], 1.0), ([b, a], 1.0)], limit=2)
        assert set(fused) == {a, b}

    def test_limit_is_applied(self) -> None:
        many = [chunk(str(i)) for i in range(10)]
        assert len(fuse_rankings([(many, 1.0)], limit=3)) == 3

    def test_empty_input(self) -> None:
        assert fuse_rankings([], limit=5) == []

    def test_a_chunk_in_one_list_still_ranks(self) -> None:
        a, b = chunk("a"), chunk("b")
        fused = fuse_rankings([([a], SEMANTIC_WEIGHT), ([b], LEXICAL_WEIGHT)], 2)
        assert fused == [a, b]
