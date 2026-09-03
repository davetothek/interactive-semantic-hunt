"""Test the incremental index use case."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from ish.adapters.vector_store.pure_python import PurePythonVectorStore
from ish.application.index import Index, IndexStats, content_hash, embed_text
from ish.application.ports.parser import ParseError
from ish.application.scan import Scan
from ish.domain.chunk import Chunk


class LineParser:
    """Emit one chunk per non-blank line."""

    language = "python"
    suffixes = frozenset({".py"})

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        if "BROKEN" in source:
            raise ParseError("cannot parse")
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


class RecordingEmbedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.seen.extend(texts)
        return [[float(len(t))] for t in texts]

    def embed_query(self, text: str) -> Sequence[float]:
        return [float(len(text))]


@pytest.fixture()
def embedder() -> RecordingEmbedder:
    return RecordingEmbedder()


@pytest.fixture()
def store() -> PurePythonVectorStore:
    return PurePythonVectorStore()


def build(embedder, store, **scan_options) -> Index:
    return Index(
        scan=Scan(parsers=[LineParser()], **scan_options),
        embedder=embedder,
        vector_store=store,
    )


class TestEmbedText:
    """Verify the text handed to the embedder."""

    def test_carries_naming_context(self) -> None:
        chunk = Chunk(
            path=Path("a.py"),
            text="body",
            kind="method",
            language="python",
            symbol="C.m",
            start_line=1,
            end_line=1,
        )
        assert embed_text(chunk) == "python method C.m:\nbody"

    def test_hash_is_stable(self) -> None:
        assert content_hash("x") == content_hash("x")

    def test_hash_separates_texts(self) -> None:
        assert content_hash("x") != content_hash("y")


class TestFirstRefresh:
    """Verify a cold index."""

    def test_indexes_everything(self, embedder, store, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("alpha\nbeta\n")
        stats = build(embedder, store).refresh(tmp_path)

        assert stats.files_seen == 1
        assert stats.files_parsed == 1
        assert stats.chunks_indexed == 2
        assert stats.vectors_embedded == 2
        assert stats.changed is True

    def test_empty_tree(self, embedder, store, tmp_path: Path) -> None:
        stats = build(embedder, store).refresh(tmp_path)
        assert stats == IndexStats(files_seen=0)
        assert stats.changed is False


class TestIncrementalRefresh:
    """Verify that a second refresh does only the outstanding work."""

    def test_unchanged_tree_does_nothing(self, embedder, store, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("alpha\n")
        index = build(embedder, store)
        index.refresh(tmp_path)

        stats = index.refresh(tmp_path)
        assert stats.files_parsed == 0
        assert stats.vectors_embedded == 0
        assert stats.changed is False

    def test_edited_file_embeds_only_new_text(
        self, embedder, store, tmp_path: Path
    ) -> None:
        target = tmp_path / "a.py"
        target.write_text("alpha\nbeta\n")
        index = build(embedder, store)
        index.refresh(tmp_path)
        embedder.seen.clear()

        target.write_text("alpha\ngamma\n")
        stats = index.refresh(tmp_path)

        assert stats.vectors_embedded == 1
        assert all("gamma" in text for text in embedder.seen)

    def test_new_file_is_added(self, embedder, store, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("alpha\n")
        index = build(embedder, store)
        index.refresh(tmp_path)

        (tmp_path / "b.py").write_text("beta\n")
        stats = index.refresh(tmp_path)

        assert stats.files_parsed == 1
        assert stats.files_seen == 2


class TestOrphans:
    """Verify that vanished files leave the index."""

    def test_deleted_file_is_removed(self, embedder, store, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("alpha\n")
        (tmp_path / "b.py").write_text("beta\n")
        index = build(embedder, store)
        index.refresh(tmp_path)

        (tmp_path / "a.py").unlink()
        stats = index.refresh(tmp_path)

        assert stats.files_removed == 1
        assert set(store.file_stamps()) == {tmp_path / "b.py"}
        assert stats.changed is True

    def test_scanning_a_subtree_keeps_the_rest(
        self, embedder, store, tmp_path: Path
    ) -> None:
        """Indexing one directory must not discard another."""
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        (tmp_path / "one" / "a.py").write_text("alpha\n")
        (tmp_path / "two" / "b.py").write_text("beta\n")

        index = build(embedder, store)
        index.refresh(tmp_path)
        assert len(store.file_stamps()) == 2

        stats = index.refresh(tmp_path / "one")
        assert stats.files_removed == 0
        assert len(store.file_stamps()) == 2


class TestFailures:
    """Verify that a bad file is skipped, not fatal."""

    def test_unparseable_file_is_skipped(self, embedder, store, tmp_path: Path) -> None:
        (tmp_path / "good.py").write_text("alpha\n")
        (tmp_path / "bad.py").write_text("BROKEN\n")

        stats = build(embedder, store).refresh(tmp_path)

        assert stats.files_seen == 2
        assert stats.files_parsed == 1
        assert [c.symbol for c in store.chunks()] == ["alpha"]

    def test_file_that_vanishes_mid_scan_is_skipped(
        self, embedder, store, tmp_path: Path, monkeypatch
    ) -> None:
        """Discovery and stat are separate steps, so a file can disappear.

        Raise what a vanishing file raises. A bare ``OSError`` carries no
        errno, and pathlib re-raises those rather than reading them as an
        absence, so the test would describe a condition that cannot
        happen.
        """
        (tmp_path / "a.py").write_text("alpha\n")
        real_stat = Path.stat

        def flaky(self, *args, **kwargs):
            if self.name == "a.py":
                raise FileNotFoundError(2, "No such file or directory")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky)
        stats = build(embedder, store).refresh(tmp_path)

        assert stats.files_seen == 0
        assert stats.vectors_embedded == 0


class TestDuplicateText:
    """Verify that identical chunks are embedded once."""

    def test_same_text_in_two_files_embeds_once(
        self, embedder, store, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text("same\n")
        (tmp_path / "b.py").write_text("same\n")

        stats = build(embedder, store).refresh(tmp_path)

        # Both files hold one chunk, but the text is identical.
        assert stats.chunks_indexed == 2
        assert stats.vectors_embedded == 1


class TestPruningIsConservative:
    """Verify that only a real removal removes an index entry.

    Absence from a scan has many causes. Pruning on absence alone
    discards an index that is still valid, so every removal must rest on
    a positive test: the file is gone, or the scan would not index it.
    """

    def test_unreadable_directory_keeps_its_files(
        self, embedder, store, tmp_path: Path
    ) -> None:
        """A permission problem must not empty the index."""
        import os

        (tmp_path / "keep.py").write_text("alpha\n")
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "inside.py").write_text("beta\n")

        index = build(embedder, store)
        index.refresh(tmp_path)
        assert len(store.file_stamps()) == 2

        os.chmod(locked, 0o000)
        try:
            stats = index.refresh(tmp_path)
        finally:
            os.chmod(locked, 0o755)

        assert stats.files_removed == 0
        assert len(store.file_stamps()) == 2

    def test_deleted_file_is_still_removed(
        self, embedder, store, tmp_path: Path
    ) -> None:
        (tmp_path / "a.py").write_text("alpha\n")
        (tmp_path / "b.py").write_text("beta\n")
        index = build(embedder, store)
        index.refresh(tmp_path)

        (tmp_path / "a.py").unlink()
        stats = index.refresh(tmp_path)

        assert stats.files_removed == 1
        assert set(store.file_stamps()) == {tmp_path / "b.py"}

    def test_newly_ignored_file_is_removed(
        self, embedder, store, tmp_path: Path
    ) -> None:
        """A file the scan no longer wants must leave the index."""
        (tmp_path / "app.py").write_text("alpha\n")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "gen.py").write_text("beta\n")

        build(embedder, store).refresh(tmp_path)
        assert len(store.file_stamps()) == 2

        narrowed = build(embedder, store, ignored_dirs=["build"])
        stats = narrowed.refresh(tmp_path)

        assert stats.files_removed == 1
        assert set(store.file_stamps()) == {tmp_path / "app.py"}

    def test_a_targeted_scan_never_prunes_a_sibling(
        self, embedder, store, tmp_path: Path
    ) -> None:
        """Searching one directory must not damage the rest of the index."""
        for name in ("one", "two"):
            directory = tmp_path / name
            directory.mkdir()
            (directory / f"{name}.py").write_text(f"{name}\n")

        index = build(embedder, store)
        index.refresh(tmp_path)
        assert len(store.file_stamps()) == 2

        stats = index.refresh(tmp_path / "one")
        assert stats.files_removed == 0
        assert len(store.file_stamps()) == 2


class TestScanAccepts:
    """Verify the predicate that pruning relies on."""

    def test_claimed_suffix(self) -> None:
        from ish.application.scan import Scan

        scan = Scan(parsers=[LineParser()])
        assert scan.accepts(Path("/x/a.py")) is True

    def test_unclaimed_suffix(self) -> None:
        from ish.application.scan import Scan

        assert Scan(parsers=[LineParser()]).accepts(Path("/x/a.txt")) is False

    def test_ignored_directory(self) -> None:
        from ish.application.scan import Scan

        scan = Scan(parsers=[LineParser()], ignored_dirs=["build"])
        assert scan.accepts(Path("/x/build/a.py")) is False

    def test_answers_without_touching_the_disk(self) -> None:
        """The caller may ask about a file it cannot read."""
        from ish.application.scan import Scan

        scan = Scan(parsers=[LineParser()])
        assert scan.accepts(Path("/definitely/not/here.py")) is True


class TestIncrementalEmbedding:
    """Verify that a long index keeps the work it has already done."""

    def test_a_failure_keeps_earlier_batches(
        self, store, tmp_path: Path, monkeypatch
    ) -> None:
        """One failed request must not discard every vector before it."""
        from ish.application import index as module

        monkeypatch.setattr(module, "EMBED_BATCH", 2)

        class Flaky:
            model_name = "flaky"

            def __init__(self) -> None:
                self.calls = 0

            def embed_documents(self, texts):
                self.calls += 1
                if self.calls > 2:
                    raise TimeoutError("the daemon stopped answering")
                return [[float(len(t))] for t in texts]

            def embed_query(self, text):
                return [1.0]

        for n in range(8):
            (tmp_path / f"f{n}.py").write_text(f"chunk{n}\n")

        with pytest.raises(TimeoutError):
            build(Flaky(), store).refresh(tmp_path)

        # Two batches of two landed before the third failed.
        assert len(store.missing_vectors([])) == 0
        assert store.missing_vectors(["nothing"]) == {"nothing"}

    def test_a_large_index_is_stored_in_batches(
        self, embedder, store, tmp_path: Path, monkeypatch
    ) -> None:
        from ish.application import index as module

        monkeypatch.setattr(module, "EMBED_BATCH", 3)
        for n in range(7):
            (tmp_path / f"f{n}.py").write_text(f"chunk{n}\n")

        stats = build(embedder, store).refresh(tmp_path)

        assert stats.vectors_embedded == 7
        assert len(store.chunks()) == 7
