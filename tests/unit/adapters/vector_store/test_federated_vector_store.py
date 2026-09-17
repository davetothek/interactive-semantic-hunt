"""Test searching several indexes as one."""

from pathlib import Path

from ish.adapters.vector_store.federated import FederatedReader
from ish.adapters.vector_store.pure_python import PurePythonVectorStore
from ish.application.ports.vector_store import FileStamp, VectorReader, VectorStore
from ish.domain.chunk import Chunk

STAMP = FileStamp(mtime_ns=1, size=1)


def chunk(symbol: str, path: str = "a.py") -> Chunk:
    return Chunk(
        path=Path(path),
        text=f"def {symbol}(): pass",
        kind="function",
        language="python",
        symbol=symbol,
        start_line=1,
        end_line=1,
    )


def store_with(*entries: tuple[str, list[float]]) -> PurePythonVectorStore:
    store = PurePythonVectorStore()
    store.add_vectors(dict(entries))
    store.set_file(
        Path(f"{entries[0][0]}.py"),
        STAMP,
        [(chunk(name, f"{name}.py"), name) for name, _ in entries],
    )
    return store


class TestPortCompliance:
    def test_reads(self) -> None:
        assert isinstance(FederatedReader([]), VectorReader)

    def test_cannot_be_written(self) -> None:
        """A search from a parent must never rewrite a subtree's index."""
        assert not isinstance(FederatedReader([]), VectorStore)


class TestReading:
    """Verify that every index is searched."""

    def test_results_come_from_all_indexes(self) -> None:
        primary = store_with(("alpha", [1.0, 0.0]))
        other = store_with(("beta", [0.9, 0.1]))
        federated = FederatedReader([primary, other])

        found = {c.symbol for c, _ in federated.search([1.0, 0.0], limit=5)}
        assert found == {"alpha", "beta"}

    def test_ranking_is_global(self) -> None:
        """The best match wins even when it is not in the primary."""
        primary = store_with(("far", [0.0, 1.0]))
        other = store_with(("near", [1.0, 0.0]))
        federated = FederatedReader([primary, other])

        results = federated.search([1.0, 0.0], limit=2)
        assert results[0][0].symbol == "near"

    def test_limit_applies_across_indexes(self) -> None:
        stores = [store_with((f"s{i}", [1.0, float(i) / 10])) for i in range(4)]
        federated = FederatedReader(stores)
        assert len(federated.search([1.0, 0.0], limit=2)) == 2

    def test_chunks_lists_every_index(self) -> None:
        federated = FederatedReader(
            [store_with(("alpha", [1.0])), store_with(("beta", [1.0]))]
        )
        assert {c.symbol for c in federated.chunks()} == {"alpha", "beta"}

    def test_a_repeated_chunk_appears_once(self) -> None:
        """A tree and its subdirectory both hold the same chunk."""
        shared = store_with(("same", [1.0, 0.0]))
        duplicate = store_with(("same", [1.0, 0.0]))
        federated = FederatedReader([shared, duplicate])

        assert len(federated.chunks()) == 1
        assert len(federated.search([1.0, 0.0], limit=5)) == 1

    def test_one_index_counts_without_listing(self) -> None:
        class Counting(PurePythonVectorStore):
            listed = 0

            def chunks(self):
                Counting.listed += 1
                return super().chunks()

        only = Counting()
        only.add_vectors({"a": [1.0]})
        only.set_file(Path("a.py"), STAMP, [(chunk("a"), "a")])
        assert FederatedReader([only]).count() == 1
        assert Counting.listed == 0

    def test_several_indexes_count_each_chunk_once(self) -> None:
        shared = store_with(("same", [1.0, 0.0]))
        duplicate = store_with(("same", [1.0, 0.0]))
        assert FederatedReader([shared, duplicate]).count() == 1

    def test_close_releases_every_index(self) -> None:
        class Closing(PurePythonVectorStore):
            closed = 0

            def close(self) -> None:
                Closing.closed += 1

        FederatedReader([Closing(), Closing()]).close()
        assert Closing.closed == 2
