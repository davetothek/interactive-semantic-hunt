"""Test the CachedEmbedder wrapper."""

from collections.abc import Sequence

from ish.adapters.embedder.cached import CachedEmbedder
from ish.application.ports.embedder import Embedder


class DummyEmbedder(Embedder):
    """A dummy embedder for testing."""

    def __init__(self) -> None:
        self.call_count = 0

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.call_count += 1
        return [[float(len(t))] for t in texts]


def test_cached_embedder(tmp_path) -> None:
    """Verify that it returns from cache and doesn't call underlying embedder."""
    dummy = DummyEmbedder()
    cached = CachedEmbedder(dummy, cache_dir=str(tmp_path / "cache"))

    # First call should hit the dummy
    results = cached.embed(["hello", "world"])
    assert dummy.call_count == 1
    assert results == [[5.0], [5.0]]

    # Second call should return from cache
    results2 = cached.embed(["hello", "world"])
    assert dummy.call_count == 1
    assert results2 == [[5.0], [5.0]]

    # Third call with new items should mix cache and dummy
    results3 = cached.embed(["hello", "foo", "world", "bar"])
    assert dummy.call_count == 2
    assert results3 == [[5.0], [3.0], [5.0], [3.0]]
    cached.close()


def test_cached_embedder_empty(tmp_path) -> None:
    """Verify empty input behaves correctly."""
    dummy = DummyEmbedder()
    cached = CachedEmbedder(dummy, cache_dir=str(tmp_path / "cache"))

    assert cached.embed([]) == []
    assert dummy.call_count == 0
    cached.close()


class ModelEmbedder(DummyEmbedder):
    """A dummy embedder with a configurable model identity."""

    def __init__(self, model_name: str) -> None:
        super().__init__()
        self.model_name = model_name


def test_cache_isolated_per_model(tmp_path) -> None:
    """Verify that two models never share cached vectors."""
    cache_dir = str(tmp_path / "cache")

    first = ModelEmbedder("model-a")
    first_cached = CachedEmbedder(first, cache_dir=cache_dir)
    first_cached.embed(["hello"])
    first_cached.close()
    assert first.call_count == 1

    second = ModelEmbedder("model-b")
    second_cached = CachedEmbedder(second, cache_dir=cache_dir)
    second_cached.embed(["hello"])
    second_cached.close()
    assert second.call_count == 1


def test_default_cache_dir_honors_xdg(monkeypatch, tmp_path) -> None:
    """Verify the default cache lands under XDG_CACHE_HOME, not the CWD."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    cached = CachedEmbedder(DummyEmbedder())
    cached.embed(["hello"])
    cached.close()

    assert (tmp_path / "ish").is_dir()


def test_construction_touches_no_disk(tmp_path) -> None:
    """Verify the cache opens lazily on first embed."""
    cache_dir = tmp_path / "cache"
    CachedEmbedder(DummyEmbedder(), cache_dir=str(cache_dir))
    assert not cache_dir.exists()


def test_close_is_idempotent(tmp_path) -> None:
    cached = CachedEmbedder(DummyEmbedder(), cache_dir=str(tmp_path / "cache"))
    cached.embed(["hello"])
    cached.close()
    cached.close()
