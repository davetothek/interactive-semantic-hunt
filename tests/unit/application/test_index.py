"""Test the incremental index use case."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from ish.adapters.vector_store.pure_python import PurePythonVectorStore
from ish.application.index import Index, IndexStats, content_hash, embed_text
from ish.application.ports.parser import ParseError
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


def build(embedder, store) -> Index:
    return Index(parsers=[LineParser()], embedder=embedder, vector_store=store)


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

    def test_unchanged_tree_does_nothing(
        self, embedder, store, tmp_path: Path
    ) -> None:
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

    def test_unparseable_file_is_skipped(
        self, embedder, store, tmp_path: Path
    ) -> None:
        (tmp_path / "good.py").write_text("alpha\n")
        (tmp_path / "bad.py").write_text("BROKEN\n")

        stats = build(embedder, store).refresh(tmp_path)

        assert stats.files_seen == 2
        assert stats.files_parsed == 1
        assert [c.symbol for c in store.chunks()] == ["alpha"]

    def test_file_that_vanishes_mid_scan_is_skipped(
        self, embedder, store, tmp_path: Path, monkeypatch
    ) -> None:
        """Discovery and stat are separate steps, so a file can disappear."""
        (tmp_path / "a.py").write_text("alpha\n")
        real_stat = Path.stat

        def flaky(self, *args, **kwargs):
            if self.name == "a.py":
                raise OSError("vanished")
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
