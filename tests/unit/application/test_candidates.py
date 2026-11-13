"""Test the candidates of an answer: the best overall, and the best of each type."""

from collections.abc import Sequence
from pathlib import Path

from ish.application.candidates import candidates
from ish.domain.chunk import Chunk
from ish.domain.match import Match


def _chunk(name: str) -> Chunk:
    return Chunk(Path(f"/t/{name}"), "", "section", "markdown", name, 1, 2)


class Index:
    """A search over fixed matches, each with its type, that records each call."""

    def __init__(self, *found: tuple[str, str, float]) -> None:
        self.found = [(kind, Match(_chunk(name), score)) for name, kind, score in found]
        self.calls: list[tuple[Sequence[str], int]] = []

    def __call__(self, text: str, kinds: Sequence[str], count: int) -> list[Match]:
        self.calls.append((tuple(kinds), count))
        return [m for kind, m in self.found if not kinds or kind in kinds][:count]


def _names(found: list[Match]) -> list[str]:
    return [match.chunk.symbol or "" for match in found]


class TestCodeReachesTheRerank:
    """A question in prose ranks every doc above the code that does the work.

    On a firmware tree, the best code chunk for a question about a test
    mode scored below the 50th candidate. The rerank never saw it.
    """

    def test_the_best_of_each_type_enters_below_the_overall_top(self) -> None:
        index = Index(
            *[(f"doc{n}", "doc", 0.9 - n / 100) for n in range(10)],
            ("test0", "test", 0.5),
            ("code0", "code", 0.4),
        )
        found = candidates(index, "q", 5)
        assert _names(found) == ["doc0", "doc1", "doc2", "test0", "code0"]


class TestCandidates:
    def test_the_best_overall_fills_the_places_the_types_leave(self) -> None:
        index = Index(
            ("a", "doc", 0.9),
            ("b", "doc", 0.8),
            ("c", "config", 0.7),
            ("d", "test", 0.6),
        )
        assert _names(candidates(index, "q", 10)) == ["a", "b", "c", "d"]

    def test_a_chunk_found_twice_is_kept_once_with_its_score(self) -> None:
        index = Index(("a", "doc", 0.9), ("b", "code", 0.5))
        assert candidates(index, "q", 5) == [
            Match(_chunk("a"), 0.9),
            Match(_chunk("b"), 0.5),
        ]

    def test_each_type_gets_a_fifth_of_the_limit(self) -> None:
        index = Index(("a", "doc", 0.9))
        candidates(index, "q", 50)
        assert index.calls == [
            ((), 50),
            (("doc",), 10),
            (("test",), 10),
            (("code",), 10),
        ]

    def test_the_limit_holds_when_the_types_ask_for_more(self) -> None:
        index = Index(("a", "doc", 0.9), ("b", "test", 0.8), ("c", "code", 0.7))
        assert _names(candidates(index, "q", 2)) == ["a", "b"]
