"""Test searching several indexes as one."""

from pathlib import Path

from ish.adapters.vector_store.federated import FederatedVectorStore
from ish.adapters.vector_store.pure_python import PurePythonVectorStore
from ish.application.ports.vector_store import FileStamp, VectorStore
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
    def test_satisfies_the_port(self) -> None:
        assert isinstance(FederatedVectorStore(None, []), VectorStore)


class TestReading:
    """Verify that every index is searched."""

    def test_results_come_from_all_indexes(self) -> None:
        primary = store_with(("alpha", [1.0, 0.0]))
        other = store_with(("beta", [0.9, 0.1]))
        federated = FederatedVectorStore(primary, [other])

        found = {c.symbol for c, _ in federated.search([1.0, 0.0], limit=5)}
        assert found == {"alpha", "beta"}

    def test_ranking_is_global(self) -> None:
        """The best match wins even when it is not in the primary."""
        primary = store_with(("far", [0.0, 1.0]))
        other = store_with(("near", [1.0, 0.0]))
        federated = FederatedVectorStore(primary, [other])

        results = federated.search([1.0, 0.0], limit=2)
        assert results[0][0].symbol == "near"

    def test_limit_applies_across_indexes(self) -> None:
        stores = [store_with((f"s{i}", [1.0, float(i) / 10])) for i in range(4)]
        federated = FederatedVectorStore(stores[0], stores[1:])
        assert len(federated.search([1.0, 0.0], limit=2)) == 2

    def test_chunks_lists_every_index(self) -> None:
        federated = FederatedVectorStore(
            store_with(("alpha", [1.0])), [store_with(("beta", [1.0]))]
        )
        assert {c.symbol for c in federated.chunks()} == {"alpha", "beta"}

    def test_a_repeated_chunk_appears_once(self) -> None:
        """A tree and its subdirectory both hold the same chunk."""
        shared = store_with(("same", [1.0, 0.0]))
        duplicate = store_with(("same", [1.0, 0.0]))
        federated = FederatedVectorStore(shared, [duplicate])

        assert len(federated.chunks()) == 1
        assert len(federated.search([1.0, 0.0], limit=5)) == 1

    def test_read_only_federation_still_searches(self) -> None:
        federated = FederatedVectorStore(None, [store_with(("alpha", [1.0, 0.0]))])
        assert federated.search([1.0, 0.0], limit=1)[0][0].symbol == "alpha"


class TestWritingReachesOnlyThePrimary:
    """Verify that a search from a parent cannot damage a subtree's index.

    Federation is read-only by design. A refresh must never rewrite or
    prune an index that belongs to a directory below the one searched.
    """

    def test_stamps_come_from_the_primary_alone(self) -> None:
        primary = store_with(("alpha", [1.0]))
        other = store_with(("beta", [1.0]))
        federated = FederatedVectorStore(primary, [other])

        assert set(federated.file_stamps()) == set(primary.file_stamps())

    def test_remove_does_not_touch_the_others(self) -> None:
        primary = store_with(("alpha", [1.0]))
        other = store_with(("beta", [1.0]))
        federated = FederatedVectorStore(primary, [other])

        federated.remove_files(list(other.file_stamps()))
        assert len(other.chunks()) == 1

    def test_clear_does_not_touch_the_others(self) -> None:
        primary = store_with(("alpha", [1.0]))
        other = store_with(("beta", [1.0]))
        FederatedVectorStore(primary, [other]).clear()

        assert other.file_stamps()
        assert not primary.file_stamps()

    def test_add_vectors_writes_to_the_primary(self) -> None:
        primary = PurePythonVectorStore()
        other = store_with(("beta", [1.0]))
        FederatedVectorStore(primary, [other]).add_vectors({"fresh": [1.0]})

        assert primary.missing_vectors(["fresh"]) == set()
        assert other.missing_vectors(["fresh"]) == {"fresh"}

    def test_set_file_writes_to_the_primary(self) -> None:
        primary = PurePythonVectorStore()
        other = store_with(("beta", [1.0]))
        federated = FederatedVectorStore(primary, [other])

        federated.set_file(Path("new.py"), STAMP, [(chunk("new"), "h")])
        assert set(primary.file_stamps()) == {Path("new.py")}
        assert Path("new.py") not in other.file_stamps()


class TestNoWritableIndex:
    """Verify the mode where a parent is searched but nothing is refreshed."""

    def test_reports_that_it_cannot_be_written(self) -> None:
        assert FederatedVectorStore(None, []).writable is False

    def test_a_primary_makes_it_writable(self) -> None:
        assert FederatedVectorStore(PurePythonVectorStore(), []).writable is True

    def test_writes_are_ignored_rather_than_failing(self) -> None:
        federated = FederatedVectorStore(None, [store_with(("a", [1.0]))])
        federated.add_vectors({"h": [1.0]})
        federated.set_file(Path("x.py"), STAMP, [])
        federated.remove_files([Path("x.py")])
        federated.clear()

    def test_everything_is_missing_when_nothing_is_writable(self) -> None:
        federated = FederatedVectorStore(None, [])
        assert federated.missing_vectors(["a", "b"]) == {"a", "b"}

    def test_close_releases_every_index(self) -> None:
        FederatedVectorStore(PurePythonVectorStore(), [PurePythonVectorStore()]).close()
