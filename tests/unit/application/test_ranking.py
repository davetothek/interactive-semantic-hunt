"""Test the ranking helpers shared by the vector store adapters."""

from pathlib import Path

import pytest

from ish.application.ranking import (
    LEXICAL_WEIGHT,
    MIN_FUSION_WIDTH,
    SEMANTIC_WEIGHT,
    fuse_rankings,
    fusion_width,
    is_code_like,
    rank,
    split_identifier,
)
from ish.domain.chunk import Chunk
from ish.domain.match import Match


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


class TestRank:
    """Verify the policy every store shares, against recorded primitives."""

    def _primitives(self, order: list[str], by_name: list[str]):
        asked: dict[str, tuple] = {}

        def semantic(vector, top, keep):
            asked["semantic"] = (top, keep)
            return [Match(chunk(name), 1.0 - n / 10) for n, name in enumerate(order)][
                :top
            ]

        def lexical(text, top, keep):
            asked["lexical"] = (text, top, keep)
            return [chunk(name) for name in by_name][:top]

        return semantic, lexical, asked

    def test_prose_asks_the_vector_half_for_the_page_only(self) -> None:
        semantic, lexical, asked = self._primitives(["a", "b", "c"], ["c"])
        keep = lambda c: True  # noqa: E731

        found = rank(
            [1.0], "a plain description", 2, keep, semantic=semantic, lexical=lexical
        )

        assert [m.chunk.symbol for m in found] == ["a", "b"]
        assert asked == {"semantic": (2, keep)}

    def test_an_identifier_fuses_both_halves(self) -> None:
        semantic, lexical, asked = self._primitives(["a", "b", "c"], ["c"])

        found = rank([1.0], "some_name", 2, None, semantic=semantic, lexical=lexical)

        # c is ranked by both halves, so it beats a, which only one saw.
        assert [m.chunk.symbol for m in found] == ["c", "a"]
        assert asked["semantic"][0] == fusion_width(2) == MIN_FUSION_WIDTH
        assert asked["lexical"] == ("some_name", MIN_FUSION_WIDTH, None)

    def test_the_score_stays_the_cosine(self) -> None:
        semantic, lexical, _ = self._primitives(["a", "b"], ["b"])
        found = rank([1.0], "some_name", 2, None, semantic=semantic, lexical=lexical)
        assert {m.chunk.symbol: m.score for m in found} == {"a": 1.0, "b": 0.9}

    def test_a_chunk_only_the_lexical_half_knows_scores_zero(self) -> None:
        semantic, lexical, _ = self._primitives(["a"], ["z"])
        found = rank([1.0], "some_name", 5, None, semantic=semantic, lexical=lexical)
        assert [(m.chunk.symbol, m.score) for m in found] == [("a", 1.0), ("z", 0.0)]

    def test_no_lexical_hit_keeps_the_vector_order(self) -> None:
        semantic, lexical, _ = self._primitives(["a", "b", "c"], [])
        found = rank([1.0], "some_name", 2, None, semantic=semantic, lexical=lexical)
        assert [m.chunk.symbol for m in found] == ["a", "b"]

    def test_the_fusion_width_grows_with_the_page(self) -> None:
        assert fusion_width(1) == MIN_FUSION_WIDTH
        assert fusion_width(100) == 400
