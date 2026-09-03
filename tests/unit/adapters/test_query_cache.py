"""Test that a recent query is not embedded twice."""

from ish.adapters.embedder.prefixes import QUERY_CACHE_SIZE, PrefixingEmbedder


class Counting(PrefixingEmbedder):
    """Count how often the backend is actually asked."""

    model_name = "plain"

    def __init__(self) -> None:
        self.calls = 0

    def _embed(self, texts):
        self.calls += 1
        return [[float(len(t))] for t in texts]


class TestQueryCache:
    def test_the_same_query_is_embedded_once(self) -> None:
        embedder = Counting()
        assert embedder.embed_query("alpha") == embedder.embed_query("alpha")
        assert embedder.calls == 1

    def test_a_different_query_is_embedded(self) -> None:
        embedder = Counting()
        embedder.embed_query("alpha")
        embedder.embed_query("beta")
        assert embedder.calls == 2

    def test_going_back_costs_nothing(self) -> None:
        """Deleting a character asks for text already seen."""
        embedder = Counting()
        for text in ("expo", "expos", "expo"):
            embedder.embed_query(text)
        assert embedder.calls == 2

    def test_the_oldest_is_dropped_first(self) -> None:
        embedder = Counting()
        for index in range(QUERY_CACHE_SIZE + 1):
            embedder.embed_query(f"q{index}")
        calls = embedder.calls
        # The first is gone, the most recent is kept.
        embedder.embed_query(f"q{QUERY_CACHE_SIZE}")
        assert embedder.calls == calls
        embedder.embed_query("q0")
        assert embedder.calls == calls + 1

    def test_documents_are_not_served_from_the_query_cache(self) -> None:
        embedder = Counting()
        embedder.embed_query("alpha")
        embedder.embed_documents(["alpha"])
        assert embedder.calls == 2

    def test_the_cache_holds_the_vector_it_returned(self) -> None:
        embedder = Counting()
        first = embedder.embed_query("alpha")
        assert embedder.embed_query("alpha") == first
