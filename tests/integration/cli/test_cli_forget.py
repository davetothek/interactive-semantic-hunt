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
