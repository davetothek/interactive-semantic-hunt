"""Test the catalog of stored indexes."""

from pathlib import Path

from ish.adapters.vector_store.catalog import IndexCatalog
from ish.adapters.vector_store.sqlite import SqliteVectorStore


def _index(directory: Path, name: str, root: Path | None) -> None:
    SqliteVectorStore(directory / f"{name}.db", model_id="m", root=root).close()


class TestNaming:
    def test_two_trees_never_share_a_file(self, tmp_path: Path) -> None:
        catalog = IndexCatalog(tmp_path)
        a = catalog.path_for(tmp_path / "one")
        b = catalog.path_for(tmp_path / "two")
        assert a != b
        assert a.name.startswith("one-")
        assert b.name.startswith("two-")

    def test_the_name_is_stable(self, tmp_path: Path) -> None:
        catalog = IndexCatalog(tmp_path)
        assert catalog.path_for(tmp_path) == catalog.path_for(tmp_path)

    def test_the_file_sits_in_the_directory(self, tmp_path: Path) -> None:
        assert (
            IndexCatalog(tmp_path / "idx").path_for(tmp_path).parent == tmp_path / "idx"
        )


class TestBelow:
    def test_finds_an_index_below_the_path(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / "sub").mkdir(parents=True)
        _index(tmp_path, "sub", project / "sub")
        assert set(IndexCatalog(tmp_path).below(project)) == {project / "sub"}

    def test_finds_the_path_itself(self, tmp_path: Path) -> None:
        _index(tmp_path, "self", tmp_path / "proj")
        assert set(IndexCatalog(tmp_path).below(tmp_path / "proj")) == {
            tmp_path / "proj"
        }

    def test_ignores_an_unrelated_tree(self, tmp_path: Path) -> None:
        _index(tmp_path, "other", tmp_path / "b")
        assert IndexCatalog(tmp_path).below(tmp_path / "a") == {}

    def test_no_directory(self, tmp_path: Path) -> None:
        assert IndexCatalog(tmp_path / "absent").below(tmp_path) == {}

    def test_a_file_without_a_root_is_skipped(self, tmp_path: Path) -> None:
        _index(tmp_path, "anon", None)
        assert IndexCatalog(tmp_path).below(tmp_path) == {}


class TestCovering:
    def test_the_nearest_ancestor_wins(self, tmp_path: Path) -> None:
        _index(tmp_path, "outer", tmp_path / "p")
        _index(tmp_path, "inner", tmp_path / "p" / "q")
        found = IndexCatalog(tmp_path).covering(tmp_path / "p" / "q" / "r")
        assert found is not None
        assert found[0] == tmp_path / "p" / "q"

    def test_the_tree_itself_does_not_cover_itself(self, tmp_path: Path) -> None:
        _index(tmp_path, "self", tmp_path / "p")
        assert IndexCatalog(tmp_path).covering(tmp_path / "p") is None

    def test_nothing_covers_an_unrelated_path(self, tmp_path: Path) -> None:
        _index(tmp_path, "other", tmp_path / "b")
        assert IndexCatalog(tmp_path).covering(tmp_path / "a") is None
