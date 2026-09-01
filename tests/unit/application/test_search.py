"""Test the Search orchestration use case against real collaborators."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from ish.adapters.vector_store.pure_python import PurePythonVectorStore
from ish.application.search import Search
from ish.domain.chunk import Chunk


class WordParser:
    """Emit one chunk per line, so a file's chunks are easy to predict."""

    language = "python"
    suffixes = frozenset({".py"})

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        return [
            Chunk(
                path=path,
                text=line,
                kind="function",
                language="python",
                symbol=line.strip(),
                start_line=n,
                end_line=n,
            )
            for n, line in enumerate(source.splitlines(), 1)
            if line.strip()
        ]


class CountingEmbedder:
    """Return a deterministic vector and record every batch it was given."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    @property
    def texts_embedded(self) -> int:
        return sum(len(batch) for batch in self.batches)

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.batches.append(list(texts))
        return [[float(len(t)), float(t.count("a"))] for t in texts]

    def embed_query(self, text: str) -> Sequence[float]:
        self.batches.append([text])
        return [float(len(text)), float(text.count("a"))]


@pytest.fixture()
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("alpha\nbeta\n")
    return tmp_path


def build(embedder: CountingEmbedder, store=None) -> Search:
    return Search(
        parsers=[WordParser()],
        embedder=embedder,
        vector_store=store or PurePythonVectorStore(),
    )


class TestSearchUseCase:
    """Verify indexing and querying."""

    def test_indexes_every_chunk(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        search = build(embedder)
        chunks = search.build_index(project)
        assert chunks is not None
        assert {c.symbol for c in chunks} == {"alpha", "beta"}

    def test_empty_tree_returns_none(
        self, embedder: CountingEmbedder, tmp_path: Path
    ) -> None:
        assert build(embedder).build_index(tmp_path) is None
        assert embedder.texts_embedded == 0

    def test_query_returns_ranked_results(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        search = build(embedder)
        search.build_index(project)
        results = search.search("alpha", limit=2)
        assert results
        assert all(isinstance(score, float) for _, score in results)

    def test_run_indexes_then_queries(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        results = build(embedder).run(project, "alpha", limit=1)
        assert len(results) == 1

    def test_run_on_empty_tree(
        self, embedder: CountingEmbedder, tmp_path: Path
    ) -> None:
        assert build(embedder).run(tmp_path, "anything") == []

    def test_query_embed_failure_returns_nothing(
        self, project: Path
    ) -> None:
        """A backend that returns no vector must not raise."""

        class SilentEmbedder(CountingEmbedder):
            def embed_query(self, text):
                return []

        search = build(SilentEmbedder())
        search.build_index(project)
        assert search.search("q") == []

    def test_close_releases_the_store(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        search = build(embedder)
        search.build_index(project)
        search.close()


class TestIncrementalBehavior:
    """Verify that a second run reuses the first run's work."""

    def test_unchanged_tree_embeds_nothing_again(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        first = embedder.texts_embedded

        build(embedder, store).build_index(project)
        assert embedder.texts_embedded == first

    def test_edited_file_embeds_only_the_new_chunk(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        before = embedder.texts_embedded

        (project / "a.py").write_text("alpha\ngamma\n")
        build(embedder, store).build_index(project)

        # "alpha" is unchanged, so only "gamma" needs a vector.
        assert embedder.texts_embedded == before + 1

    def test_deleted_file_is_dropped(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        (project / "a.py").unlink()

        assert build(embedder, store).build_index(project) is None
        assert store.file_stamps() == {}

    def test_renamed_file_reuses_vectors(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        """Content-keyed vectors survive a move."""
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        before = embedder.texts_embedded

        (project / "a.py").rename(project / "b.py")
        build(embedder, store).build_index(project)

        assert embedder.texts_embedded == before
        assert set(store.file_stamps()) == {project / "b.py"}

    def test_reindex_rebuilds_without_re_embedding(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        before = embedder.texts_embedded

        forced = Search(
            parsers=[WordParser()],
            embedder=embedder,
            vector_store=store,
            reindex=True,
        )
        chunks = forced.build_index(project)

        assert chunks is not None
        assert embedder.texts_embedded == before


class TestResultFilters:
    """Verify the query-scope filters.

    These narrow what a search returns. They must never reach the index,
    or the next run would prune everything they exclude.
    """

    @pytest.fixture()
    def mixed(self, tmp_path: Path) -> Path:
        (tmp_path / "src").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "src" / "a.py").write_text("alpha\n")
        (tmp_path / "docs" / "b.py").write_text("beta\n")
        return tmp_path

    def _search(self, embedder, mixed: Path, **kwargs) -> Search:
        return Search(
            parsers=[WordParser()],
            embedder=embedder,
            vector_store=PurePythonVectorStore(),
            **kwargs,
        )

    def test_no_filter_returns_everything(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed)
        search.build_index(mixed)
        assert len(search.all_chunks()) == 2

    def test_under_narrows_by_path(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, under="/docs/")
        search.build_index(mixed)
        assert [c.symbol for c in search.all_chunks()] == ["beta"]

    def test_lang_narrows_by_language(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, lang=["nothing"])
        search.build_index(mixed)
        assert search.all_chunks() == []

    def test_lang_keeps_a_matching_language(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, lang=["python"])
        search.build_index(mixed)
        assert len(search.all_chunks()) == 2

    def test_search_respects_the_filter(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, under="/docs/")
        search.build_index(mixed)
        results = search.search("alpha", limit=5)
        assert all("/docs/" in str(c.path) for c, _ in results)

    def test_the_filter_does_not_shrink_the_index(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        """A narrowed query must leave every file indexed."""
        store = PurePythonVectorStore()
        Search(
            parsers=[WordParser()],
            embedder=embedder,
            vector_store=store,
            under="/docs/",
        ).build_index(mixed)

        assert len(store.file_stamps()) == 2
        assert len(store.chunks()) == 2

    def test_invalid_under_expression_is_reported(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        with pytest.raises(ValueError, match="'under'"):
            self._search(embedder, mixed, under="(unclosed")
