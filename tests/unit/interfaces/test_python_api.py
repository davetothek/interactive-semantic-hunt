"""Test the Python API, which wires the same use cases the CLI wires."""

from pathlib import Path

import pytest

from ish.interfaces.python.api import Ish
from ish.settings import Settings


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("def parse_config():\n    return 1\n")
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\nHow to configure it.\n")
    (tmp_path / "tests" / "test_app.py").write_text("def test_parse():\n    pass\n")
    return tmp_path


@pytest.fixture()
def offline(monkeypatch) -> None:
    """Replace the embedder so no model or daemon is needed."""
    from ish import bootstrap

    class Fake:
        model_id = "fake"

        def embed_documents(self, texts):
            return [[float(len(t)), 1.0] for t in texts]

        def embed_query(self, text):
            return [float(len(text)), 1.0]

    monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())


def _ish(project: Path) -> Ish:
    return Ish(project, settings=Settings(no_cache=True, git=False))


class TestLifetime:
    def test_path_is_resolved(self, project: Path) -> None:
        assert _ish(project).path == project.resolve()

    def test_context_manager_closes(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert ish._search is not None
        assert ish._search is None

    def test_reopens_after_close(self, project: Path, offline) -> None:
        ish = _ish(project)
        ish.index()
        ish.close()
        assert ish.index() > 0

    def test_overrides_apply(self, project: Path) -> None:
        assert Ish(project, settings=Settings(), limit=42).settings.limit == 42

    def test_repr_names_the_path(self, project: Path) -> None:
        assert str(project.resolve()) in repr(_ish(project))


class TestReading:
    def test_index_reports_a_count(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            assert ish.index() > 0

    def test_search_returns_ranked_pairs(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            results = ish.search("configure", limit=3)
            assert results
            assert all(isinstance(score, float) for _chunk, score in results)

    def test_chunks_lists_everything(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert len(ish.chunks()) == ish.index()

    def test_a_filter_narrows_the_listing(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert all(c.language == "markdown" for c in ish.chunks(lang=["markdown"]))

    def test_a_type_filter_narrows_the_listing(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            names = {c.path.name for c in ish.chunks(type=["doc"])}
            assert names == {"guide.md"}

    def test_a_filter_in_the_query_wins(self, project: Path, offline) -> None:
        """A word typed into the query beats the argument."""
        with _ish(project) as ish:
            ish.index()
            results = ish.search("type:doc configure", limit=5, type=["code"])
            assert all(c.path.name == "guide.md" for c, _ in results)

    def test_a_language_alias_is_accepted(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert ish.chunks(lang=["md"]) == ish.chunks(lang=["markdown"])


class TestStatus:
    def test_status_counts_what_is_indexed(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            status = ish.status()
            assert status["path"] == project.resolve()
            assert status["chunks"] > 0
            assert status["files"] == 3
            assert set(status["languages"]) == {"python", "markdown"}
            assert set(status["types"]) == {"code", "doc", "test"}

    def test_the_counts_add_up(self, project: Path, offline) -> None:
        """Every chunk falls into exactly one type."""
        with _ish(project) as ish:
            ish.index()
            status = ish.status()
            assert sum(status["types"].values()) == status["chunks"]
            assert sum(status["languages"].values()) == status["chunks"]
