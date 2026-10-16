"""Test removing a stored index from the command line."""

import shutil
from pathlib import Path

import pytest

from ish.adapters.vector_store.sqlite import SqliteVectorStore
from ish.interfaces.cli.main import main


@pytest.fixture()
def indexed(tmp_path: Path, monkeypatch) -> Path:
    """A tree with a stored index, in an index directory of its own."""
    tree = tmp_path / "proj"
    tree.mkdir()
    indexes = tmp_path / "idx"
    SqliteVectorStore(indexes / "proj.db", model_id="m", root=tree.resolve()).close()
    monkeypatch.setenv("ISH_CACHE_DIR", str(indexes))
    return tree


class TestForget:
    def test_the_index_is_removed(self, indexed: Path, tmp_path: Path, capsys) -> None:
        assert main(["--forget", str(indexed)]) == 0
        assert list((tmp_path / "idx").glob("*.db")) == []
        assert "Removed the index" in capsys.readouterr().err

    def test_a_gone_tree_can_still_be_forgotten(
        self, indexed: Path, tmp_path: Path
    ) -> None:
        shutil.rmtree(indexed)
        assert main(["--forget", str(indexed)]) == 0
        assert list((tmp_path / "idx").glob("*.db")) == []

    def test_a_tree_without_an_index_is_an_error(
        self, indexed: Path, tmp_path: Path, caplog
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        assert main(["--forget", str(other)]) == 1
        assert "No stored index" in caplog.text
        assert list((tmp_path / "idx").glob("*.db")) == [tmp_path / "idx" / "proj.db"]


class TestSweep:
    def test_the_orphans_go_on_request(
        self, indexed: Path, tmp_path: Path, capsys, monkeypatch
    ) -> None:
        from ish import bootstrap

        class Backend:
            model_name = "stub"

            @classmethod
            def from_option(cls, model, context_tokens=None):
                return cls()

            def embed_documents(self, texts):
                return [[1.0] for _ in texts]

            def embed_query(self, text):
                return [1.0]

        monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", Backend)
        store = SqliteVectorStore(tmp_path / "idx" / "proj.db", model_id="m")
        store.add_vectors({"orphan": [1.0]})
        store.close()
        assert main(["--sweep", str(indexed)]) == 0
        assert "Swept 1 vector" in capsys.readouterr().err

    def test_a_tree_without_an_index_is_an_error(
        self, indexed: Path, tmp_path: Path, caplog
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        assert main(["--sweep", str(other)]) == 1
        assert "No stored index" in caplog.text
