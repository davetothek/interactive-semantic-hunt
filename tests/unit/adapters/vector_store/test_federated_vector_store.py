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

    def test_disjoint_indexes_count_without_listing(self) -> None:
        """Indexes of trees that do not nest cannot share a chunk.

        Listing 72,995 chunks to count them cost 0.92 s a query on a
        root that read 7 sibling indexes. A sum of counts costs a row
        count each.
        """

        class Counting(PurePythonVectorStore):
            listed = 0

            def chunks(self):
                Counting.listed += 1
                return super().chunks()

        stores = []
        for name in ("alpha", "beta", "gamma"):
            store = Counting()
            store.add_vectors({name: [1.0]})
            store.set_file(Path(f"{name}.py"), STAMP, [(chunk(name), name)])
            stores.append(store)

        assert FederatedReader(stores, disjoint=True).count() == 3
        assert Counting.listed == 0

    def test_indexed_paths_join_every_index_once(self) -> None:
        one = store_with(("alpha", [1.0]))
        two = store_with(("alpha", [1.0]))
        two.set_file(Path("empty.adoc"), STAMP, [])
        federated = FederatedReader([one, two])
        assert federated.indexed_paths() == [Path("alpha.py"), Path("empty.adoc")]

    def test_the_same_chunk_in_two_checkouts_is_shown_once(self) -> None:
        """Two checkouts hold the chunk under two paths. One result, from the first."""
        repo = Path("/repo")
        main = store_with(("same", [1.0, 0.0]))
        main.set_file(
            Path("/main/src/same.py"),
            STAMP,
            [(chunk("same", "/main/src/same.py"), "same")],
        )
        other = store_with(("same", [1.0, 0.0]))
        other.set_file(
            Path("/wt/src/same.py"), STAMP, [(chunk("same", "/wt/src/same.py"), "same")]
        )
        federated = FederatedReader(
            [main, other],
            scopes=[(repo / ".git", Path("/main")), (repo / ".git", Path("/wt"))],
        )
        results = federated.search([1.0, 0.0], limit=5)
        shown = [c.path for c, _ in results if c.path.is_absolute()]
        assert shown == [Path("/main/src/same.py")]

    def test_the_same_path_in_two_repositories_is_two_results(self) -> None:
        one = store_with(("alpha", [1.0, 0.0]))
        one.set_file(Path("/a/src/x.py"), STAMP, [(chunk("x", "/a/src/x.py"), "alpha")])
        two = store_with(("beta", [1.0, 0.0]))
        two.set_file(Path("/b/src/x.py"), STAMP, [(chunk("x", "/b/src/x.py"), "beta")])
        federated = FederatedReader(
            [one, two],
            scopes=[(Path("/a/.git"), Path("/a")), (Path("/b/.git"), Path("/b"))],
        )
        found = {c.path for c, _ in federated.search([1.0, 0.0], limit=5)}
        assert {Path("/a/src/x.py"), Path("/b/src/x.py")} <= found

    def test_without_scopes_a_path_names_the_chunk(self) -> None:
        one = store_with(("alpha", [1.0, 0.0]))
        one.set_file(Path("/a/x.py"), STAMP, [(chunk("x", "/a/x.py"), "alpha")])
        two = store_with(("beta", [1.0, 0.0]))
        two.set_file(Path("/b/x.py"), STAMP, [(chunk("x", "/b/x.py"), "beta")])
        federated = FederatedReader([one, two])
        found = {c.path for c, _ in federated.search([1.0, 0.0], limit=5)}
        assert {Path("/a/x.py"), Path("/b/x.py")} <= found

    def test_close_releases_every_index(self) -> None:
        class Closing(PurePythonVectorStore):
            closed = 0

            def close(self) -> None:
                Closing.closed += 1

        FederatedReader([Closing(), Closing()]).close()
        assert Closing.closed == 2
