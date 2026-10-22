"""Test what an index records about where its tree belongs.

The file name carries only a hash of the tree's path. The tree, the
repository, and the checkout are recorded inside, so a search can tell
two checkouts of one repository apart without asking git again.
"""

import sqlite3
from pathlib import Path

import pytest

from ish.adapters.vector_store.sqlite import SqliteVectorStore


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "index" / "test.db"


class TestRecording:
    def test_the_repository_and_checkout_are_recorded(
        self, db_path: Path, tmp_path: Path
    ) -> None:
        SqliteVectorStore(
            db_path,
            model_id="m",
            root=tmp_path / "wt" / "src",
            repository=tmp_path / ".git",
            checkout=tmp_path / "wt",
        ).close()
        assert SqliteVectorStore.read_identity(db_path) == {
            "root": tmp_path / "wt" / "src",
            "repository": tmp_path / ".git",
            "checkout": tmp_path / "wt",
        }

    def test_only_what_is_known_is_recorded(
        self, db_path: Path, tmp_path: Path
    ) -> None:
        SqliteVectorStore(db_path, model_id="m", root=tmp_path).close()
        assert SqliteVectorStore.read_identity(db_path) == {"root": tmp_path}

    def test_a_later_opening_adds_what_it_knows(
        self, db_path: Path, tmp_path: Path
    ) -> None:
        """An index made before repositories were recorded learns its own."""
        SqliteVectorStore(db_path, model_id="m", root=tmp_path).close()
        SqliteVectorStore(
            db_path, model_id="m", root=tmp_path, repository=tmp_path / ".git"
        ).close()
        identity = SqliteVectorStore.read_identity(db_path)
        assert identity["repository"] == tmp_path / ".git"
        assert identity["root"] == tmp_path

    def test_reopening_with_the_same_identity_writes_nothing(
        self, db_path: Path, tmp_path: Path
    ) -> None:
        """A write on open would stall behind an index run, for nothing."""
        kwargs = {
            "model_id": "m",
            "root": tmp_path,
            "repository": tmp_path / ".git",
            "checkout": tmp_path,
        }
        SqliteVectorStore(db_path, **kwargs).close()
        watcher = sqlite3.connect(db_path)
        try:
            before = watcher.execute("PRAGMA data_version").fetchone()[0]
            for _ in range(3):
                SqliteVectorStore(db_path, **kwargs).close()
            after = watcher.execute("PRAGMA data_version").fetchone()[0]
        finally:
            watcher.close()
        assert after == before

    def test_nothing_known_records_nothing(self, db_path: Path) -> None:
        SqliteVectorStore(db_path, model_id="m").close()
        assert SqliteVectorStore.read_identity(db_path) == {}


class TestReading:
    def test_a_missing_file_names_nothing(self, tmp_path: Path) -> None:
        assert SqliteVectorStore.read_identity(tmp_path / "absent.db") == {}

    def test_a_file_that_is_not_an_index_names_nothing(self, tmp_path: Path) -> None:
        junk = tmp_path / "junk.db"
        junk.write_text("not a database")
        assert SqliteVectorStore.read_identity(junk) == {}
