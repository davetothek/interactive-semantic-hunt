"""Test the VectorStore protocol contract."""

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from ish.application.ports.vector_store import FileStamp, VectorStore
from ish.domain.chunk import Chunk


class FakeVectorStore:
    """Minimal implementation that satisfies the VectorStore protocol."""

    def file_stamps(self) -> Mapping[Path, FileStamp]:
        return {}

    def missing_vectors(self, hashes: Collection[str]) -> set[str]:
        return set(hashes)

    def add_vectors(self, vectors: Mapping[str, Sequence[float]]) -> None:
        return None

    def set_file(
        self, path: Path, stamp: FileStamp, chunks: Sequence[tuple[Chunk, str]]
    ) -> None:
        return None

    def remove_files(self, paths: Collection[Path]) -> None:
        return None

    def chunks(self) -> Sequence[Chunk]:
        return []

    def search(
        self, query_vector: Sequence[float], limit: int = 5
    ) -> Sequence[tuple[Chunk, float]]:
        return []

    def clear(self) -> None:
        return None

    def close(self) -> None:
        return None


class TestVectorStoreProtocol:
    """Verify that the protocol accepts a conforming implementation."""

    def test_fake_vector_store_is_instance(self) -> None:
        """Confirm runtime_checkable works."""
        assert isinstance(FakeVectorStore(), VectorStore)

    def test_real_adapter_is_instance(self) -> None:
        """Confirm the shipped in-memory adapter satisfies the port."""
        from ish.adapters.vector_store.pure_python import PurePythonVectorStore

        assert isinstance(PurePythonVectorStore(), VectorStore)

    def test_sqlite_adapter_is_instance(self, tmp_path: Path) -> None:
        """Confirm the persistent adapter satisfies the same port."""
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        store = SqliteVectorStore(tmp_path / "i.db", model_id="m")
        try:
            assert isinstance(store, VectorStore)
        finally:
            store.close()


class TestFileStamp:
    """Verify the stamp value object."""

    def test_equal_stamps(self) -> None:
        assert FileStamp(mtime_ns=1, size=2) == FileStamp(mtime_ns=1, size=2)

    def test_size_change_differs(self) -> None:
        """A file edited to the same mtime but a new size is still stale."""
        assert FileStamp(mtime_ns=1, size=2) != FileStamp(mtime_ns=1, size=3)

    def test_mtime_change_differs(self) -> None:
        assert FileStamp(mtime_ns=1, size=2) != FileStamp(mtime_ns=9, size=2)
