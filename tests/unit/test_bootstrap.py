"""Test the composition root."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ish import bootstrap
from ish.adapters.parser import PARSERS
from ish.application.ports.parser import Parser
from ish.application.scan import Scan
from ish.settings import (
    CONFIG_BASENAME,
    CONFIG_DIRNAME,
    CONFIG_FILENAME,
    Settings,
    load_settings,
)


def test_default_embedder_is_registered() -> None:
    assert Settings().embedder in bootstrap.EMBEDDERS


def test_build_parsers_satisfy_the_port() -> None:
    parsers = bootstrap.build_parsers(Settings())
    assert parsers
    assert all(isinstance(p, Parser) for p in parsers)


def test_build_scan_wires_the_use_case(tmp_path) -> None:
    assert isinstance(bootstrap.build_scan(Settings(), tmp_path), Scan)


def test_unknown_embedder_is_reported() -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="Unknown embedder"):
        bootstrap.build_embedder(replace(Settings(), embedder="nope"))


class TestRegistries:
    """Verify the registries beside the adapters are what bootstrap selects from."""

    def test_the_backend_table_is_the_one_in_the_embedder_package(self) -> None:
        from ish.adapters import embedder

        assert bootstrap.EMBEDDERS is embedder.EMBEDDERS

    def test_every_backend_reads_the_model_option(self) -> None:
        """A registry entry takes the option and answers with a backend."""
        backend = bootstrap.EMBEDDERS["ollama"]
        assert (
            backend.from_option("mxbai-embed-large").model_name == "mxbai-embed-large"
        )

    def test_the_extras_are_not_imported_by_the_registry(self) -> None:
        """Listing the backends must not import a package that may be absent."""
        import sys

        assert "llama_cpp" not in sys.modules
        assert "sentence_transformers" not in sys.modules

    def test_every_registered_language_can_be_built(self) -> None:
        built = bootstrap.build_parsers(Settings())
        assert {p.language for p in built} == set(PARSERS)

    def test_the_vocabulary_comes_from_the_registry(self) -> None:
        """No table outside the registry says what a language is called."""
        words = bootstrap.build_vocabulary(Settings())
        assert words.resolve("c") == "cpp"
        assert words.resolve("yml") == "yaml"
        assert "adoc" in words.spellings
        assert set(words.spellings) >= set(PARSERS)

    def test_the_vocabulary_sorts_by_what_a_language_holds(self) -> None:
        from pathlib import Path as _Path

        from ish.domain.chunk import Chunk

        words = bootstrap.build_vocabulary(Settings())
        doc = Chunk(
            path=_Path("/p/guide.md"),
            text="x",
            kind="section",
            language="markdown",
            symbol="x",
            start_line=1,
            end_line=1,
        )
        assert words.categorize(doc) == "doc"

    def test_a_type_pattern_still_wins_over_the_registry(self) -> None:
        from pathlib import Path as _Path

        from ish.domain.chunk import Chunk

        words = bootstrap.build_vocabulary(
            replace(Settings(), type_patterns=("test:/Verification/",))
        )
        chunk = Chunk(
            path=_Path("/p/Verification/guide.md"),
            text="x",
            kind="section",
            language="markdown",
            symbol="x",
            start_line=1,
            end_line=1,
        )
        assert words.categorize(chunk) == "test"

    def test_build_embedder_reads_the_model_option(self) -> None:
        settings = replace(Settings(), embedder="ollama", model="mxbai-embed-large")
        assert bootstrap.build_embedder(settings).model_name == "mxbai-embed-large"

    def test_build_embedder_falls_back_to_the_backend_default(self) -> None:
        from ish.adapters.embedder.ollama import DEFAULT_MODEL

        settings = replace(Settings(), embedder="ollama", model="")
        assert bootstrap.build_embedder(settings).model_name == DEFAULT_MODEL


class TestLanguageSelection:
    """Verify that the languages option chooses which parsers are built."""

    def test_empty_enables_every_parser(self) -> None:
        from dataclasses import replace

        built = bootstrap.build_parsers(replace(Settings(), languages=()))
        assert {p.language for p in built} == set(PARSERS)

    def test_named_language_is_the_only_one_built(self) -> None:
        from dataclasses import replace

        built = bootstrap.build_parsers(replace(Settings(), languages=("python",)))
        assert [p.language for p in built] == ["python"]

    def test_unknown_language_is_reported(self) -> None:
        from dataclasses import replace

        with pytest.raises(ValueError, match="Unknown language"):
            bootstrap.build_parsers(replace(Settings(), languages=("cobol",)))

    def test_an_alias_selects_the_language_it_names(self) -> None:
        """`--languages c` and `lang:c` must name one parser."""
        built = bootstrap.build_parsers(replace(Settings(), languages=("c",)))
        assert [p.language for p in built] == ["cpp"]


class TestVectorStoreWiring:
    """Verify how the store is chosen and where the index lands."""

    def test_no_cache_uses_the_in_memory_store(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.pure_python import PurePythonVectorStore

        settings = replace(Settings(), no_cache=True)
        primary, reader = bootstrap.build_stores(settings, tmp_path, _StubEmbedder())
        try:
            assert isinstance(reader, PurePythonVectorStore)
            assert primary is reader
        finally:
            reader.close()

    def test_default_persists_to_sqlite(self, tmp_path, monkeypatch) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        settings = replace(Settings(), cache_dir=str(tmp_path / "idx"))
        primary, reader = bootstrap.build_stores(settings, tmp_path, _StubEmbedder())
        try:
            assert isinstance(reader, SqliteVectorStore)
            assert primary is reader
        finally:
            reader.close()
        assert list((tmp_path / "idx").glob("*.db"))

    def test_a_new_index_records_its_repository(self, tmp_path, monkeypatch) -> None:
        """Two checkouts of one repository must be told apart later."""
        import subprocess
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        tree = tmp_path / "repo"
        tree.mkdir()
        subprocess.run(["git", "-C", str(tree), "init", "-q"], check=True)
        indexes = tmp_path / "idx"
        settings = replace(Settings(), cache_dir=str(indexes))
        _primary, reader = bootstrap.build_stores(settings, tree, _StubEmbedder())
        reader.close()

        (db_path,) = [
            p for p in indexes.glob("*.db") if not p.name.startswith("vectors-")
        ]
        identity = SqliteVectorStore.read_identity(db_path)
        assert identity["repository"] == (tree / ".git").resolve()
        assert identity["checkout"] == tree.resolve()

    def test_an_index_that_knows_its_repository_asks_git_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        """Opening seven indexes must not cost seven git commands.

        One command names the checkout the search starts in. The
        indexes below it have recorded their own.
        """
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        for name in ("one", "two"):
            (project / name).mkdir(parents=True)
            SqliteVectorStore(
                indexes / f"{name}.db",
                model_id="m",
                root=project / name,
                repository=tmp_path / ".git",
                checkout=project,
            ).close()
        asked: list[Path] = []

        def counted(root):
            asked.append(root)
            return None, None

        monkeypatch.setattr(bootstrap, "repository_identity", counted)
        settings = replace(Settings(), cache_dir=str(indexes))
        _primary, reader = bootstrap.build_stores(settings, project, _StubEmbedder())
        reader.close()
        assert asked == [project.resolve()]

    def test_trees_of_one_repository_share_a_vectors_store(self, tmp_path) -> None:
        """The second tree of a repository finds what the first embedded."""
        import subprocess
        from dataclasses import replace

        repo = tmp_path / "repo"
        for name in ("one", "two"):
            (repo / name).mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        indexes = tmp_path / "idx"
        settings = replace(Settings(), cache_dir=str(indexes))

        first, _reader = bootstrap.build_stores(settings, repo / "one", _StubEmbedder())
        assert first is not None
        first.add_vectors({"h1": [1.0, 0.0]})
        first.close()

        second, reader = bootstrap.build_stores(settings, repo / "two", _StubEmbedder())
        try:
            assert second is not None
            assert second.missing_vectors({"h1"}) == set()
        finally:
            reader.close()
        assert len(list(indexes.glob("vectors-*.db"))) == 1

    def test_a_search_reads_only_its_own_checkout(self, tmp_path) -> None:
        """A worktree under the main checkout must not join a search of it."""
        import subprocess

        from ish.adapters.vector_store.catalog import IndexCatalog
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        main = tmp_path / "main"
        (main / "src").mkdir(parents=True)
        for arguments in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "Test"],
            ["add", "."],
            ["commit", "-qm", "first", "--allow-empty"],
            [
                "worktree",
                "add",
                "-q",
                "-b",
                "wt",
                str(main / ".claude" / "worktrees" / "wt"),
            ],
        ):
            subprocess.run(["git", "-C", str(main), *arguments], check=True)
        worktree = main / ".claude" / "worktrees" / "wt"
        (worktree / "src").mkdir()
        common = (main / ".git").resolve()
        indexes = tmp_path / "idx"
        indexes.mkdir()
        for name, tree, checkout in (
            ("main-src", main / "src", main),
            ("wt-src", worktree / "src", worktree),
        ):
            SqliteVectorStore(
                indexes / f"{name}.db",
                model_id="m",
                root=tree.resolve(),
                repository=common,
                checkout=checkout.resolve(),
            ).close()
        catalog = IndexCatalog(indexes)

        # A submodule is another repository inside the tree, not another
        # checkout of this one. It stays in a search of the tree above.
        (main / "vendor" / "lib").mkdir(parents=True)
        SqliteVectorStore(
            indexes / "lib.db",
            model_id="m",
            root=(main / "vendor" / "lib").resolve(),
            repository=(main / ".git" / "modules" / "lib").resolve(),
            checkout=(main / "vendor" / "lib").resolve(),
        ).close()

        from_main = {e.tree for e in bootstrap.indexes_for(catalog, main)}
        assert from_main == {
            (main / "src").resolve(),
            (main / "vendor" / "lib").resolve(),
        }
        from_worktree = [e.tree for e in bootstrap.indexes_for(catalog, worktree)]
        assert from_worktree == [(worktree / "src").resolve()]
        from_above = [e.tree for e in bootstrap.indexes_for(catalog, tmp_path)]
        assert set(from_above) == {
            (main / "src").resolve(),
            (worktree / "src").resolve(),
            (main / "vendor" / "lib").resolve(),
        }

    def test_a_scan_judges_its_filters_from_the_checkout(self, tmp_path) -> None:
        """A worktree under .claude/worktrees/ is indexable with the main config."""
        import subprocess
        from dataclasses import replace

        main = tmp_path / "main"
        main.mkdir()
        for arguments in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "Test"],
            ["commit", "-qm", "first", "--allow-empty"],
            [
                "worktree",
                "add",
                "-q",
                "-b",
                "wt",
                str(main / ".claude" / "worktrees" / "wt"),
            ],
        ):
            subprocess.run(["git", "-C", str(main), *arguments], check=True)
        worktree = main / ".claude" / "worktrees" / "wt"
        (worktree / "src").mkdir()
        (worktree / "src" / "app.py").write_text("def app():\n    pass\n")
        settings = replace(Settings(), ignore=(".git", ".claude"), git=False)

        in_worktree = bootstrap.build_scan(settings, worktree / "src").run(
            worktree / "src"
        )
        assert [c.path.name for c in in_worktree] == ["app.py"]
        in_main = bootstrap.build_scan(settings, main).run(main)
        assert in_main == []

    def test_an_index_without_a_checkout_joins_every_search(self, tmp_path) -> None:
        """An index from before checkouts were recorded is read as before."""
        import subprocess

        from ish.adapters.vector_store.catalog import IndexCatalog
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        indexes = tmp_path / "idx"
        indexes.mkdir()
        SqliteVectorStore(
            indexes / "old.db", model_id="m", root=(repo / "src").resolve()
        ).close()
        found = bootstrap.indexes_for(IndexCatalog(indexes), repo)
        assert [e.tree for e in found] == [(repo / "src").resolve()]

    def test_outside_a_repository_nothing_is_recorded(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        tree = tmp_path / "plain"
        tree.mkdir()
        indexes = tmp_path / "idx"
        settings = replace(Settings(), cache_dir=str(indexes))
        _primary, reader = bootstrap.build_stores(settings, tree, _StubEmbedder())
        reader.close()
        (db_path,) = indexes.glob("*.db")
        assert SqliteVectorStore.read_identity(db_path) == {"root": tree.resolve()}

    def test_cache_dir_option_wins(self, tmp_path) -> None:
        from dataclasses import replace

        settings = replace(Settings(), cache_dir=str(tmp_path / "here"))
        assert bootstrap.index_dir(settings) == tmp_path / "here"

    def test_default_dir_honors_xdg(self, tmp_path, monkeypatch) -> None:
        """The index is data, not cache, so a cleanup cannot discard it."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert bootstrap.index_dir(Settings()) == tmp_path / "ish"

    def test_the_catalog_lives_in_the_index_directory(self, tmp_path) -> None:
        settings = replace(Settings(), cache_dir=str(tmp_path / "here"))
        assert bootstrap.catalog(settings).directory == tmp_path / "here"

    def test_model_id_tracks_the_adapter(self) -> None:
        from dataclasses import replace

        settings = replace(Settings(), embedder="llama.cpp")
        assert bootstrap.model_id(settings, _StubEmbedder("g")) == "llama.cpp:g"

    def test_model_id_without_a_named_model(self) -> None:
        assert bootstrap.model_id(Settings(), _StubEmbedder("")) == "ollama:default"


class _StubEmbedder:
    def __init__(self, model_name: str = "stub") -> None:
        self.model_name = model_name

    def embed_documents(self, texts):
        return [[1.0] for _ in texts]

    def embed_query(self, text):
        return [1.0]


class TestContextWindow:
    """Verify the window reaches the cap, the model id, and the backend.

    A model given more of a text produces another vector for it, so the
    two must never mix, and the chunk cap must follow the window or the
    model reads the same 8,000 characters as before.
    """

    def test_the_default_asks_the_backend_for_nothing(self) -> None:
        assert bootstrap.context_window(Settings()) is None

    def test_a_wider_window_is_passed_through(self) -> None:
        assert bootstrap.context_window(replace(Settings(), context_tokens=8192)) == (
            8192
        )

    def test_the_model_id_names_a_window_that_is_not_the_default(self) -> None:
        wide = replace(Settings(), context_tokens=8192)
        assert bootstrap.model_id(wide, _StubEmbedder("g")) == "ollama:g@8192"
        assert bootstrap.model_id(Settings(), _StubEmbedder("g")) == "ollama:g"

    def test_the_cap_follows_the_window(self) -> None:
        from ish.adapters.parser.structured import StructuredParser

        built = bootstrap.build_parsers(replace(Settings(), context_tokens=8192))
        sizes = {p._inner._limit for p in built}
        assert sizes == {32_000}
        structured = [
            p._inner._inner
            for p in built
            if isinstance(p._inner._inner, StructuredParser)
        ]
        assert structured and all(p.limit == 32_000 for p in structured)

    def test_the_stamp_follows_the_window(self) -> None:
        assert bootstrap.chunking_stamp(
            replace(Settings(), context_tokens=8192)
        ) != bootstrap.chunking_stamp(Settings())

    def test_the_backend_is_built_with_the_window(self, monkeypatch) -> None:
        asked: list[tuple[str, int | None]] = []

        class Recording:
            model_name = "r"

            @classmethod
            def from_option(cls, model, context_tokens=None):
                asked.append((model, context_tokens))
                return cls()

        monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", Recording)
        bootstrap.build_embedder(replace(Settings(), model="m", context_tokens=8192))
        bootstrap.build_embedder(replace(Settings(), model="m"))
        assert asked == [("m", 8192), ("m", None)]


class TestChunkingStamp:
    """Verify the stamp carries every input to how a file divides."""

    def test_the_stamp_names_the_version_and_the_caps(self) -> None:
        from ish.adapters.parser import CHUNKING_VERSION
        from ish.adapters.parser._limits import MAX_CHUNK_CHARS

        assert bootstrap.chunking_stamp(Settings()) == (
            f"{CHUNKING_VERSION}:{MAX_CHUNK_CHARS}:1000"
        )

    def test_a_different_count_cap_is_a_different_stamp(self) -> None:
        assert bootstrap.chunking_stamp(
            replace(Settings(), max_chunks=5)
        ) != bootstrap.chunking_stamp(Settings())

    def test_the_index_is_built_with_the_stamp(self, tmp_path) -> None:
        settings = replace(Settings(), no_cache=True)
        search = bootstrap.build_search(settings, tmp_path)
        try:
            assert search._index._chunking == bootstrap.chunking_stamp(settings)
        finally:
            search.close()


class TestGitAwareness:
    """Verify how the git filter is wired."""

    def test_enabled_by_default(self, tmp_path) -> None:
        assert bootstrap.build_ignored_by(Settings(), tmp_path) is not None

    def test_disabled_by_the_option(self, tmp_path) -> None:
        from dataclasses import replace

        settings = replace(Settings(), git=False)
        assert bootstrap.build_ignored_by(settings, tmp_path) is None


class TestIndexDiscovery:
    """Verify that a search finds the indexes below the path it is given."""

    def _make(self, tmp_path, name: str, root):
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        SqliteVectorStore(tmp_path / f"{name}.db", model_id="m", root=root).close()

    def test_finds_an_index_below_the_path(self, tmp_path, monkeypatch) -> None:
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "sub").mkdir(parents=True)
        self._make(indexes, "sub", project / "sub")

        settings = replace(Settings(), cache_dir=str(indexes))
        found = bootstrap.catalog(settings).below(project)
        assert set(found) == {project / "sub"}

    def test_ignores_an_unrelated_tree(self, tmp_path) -> None:
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        self._make(indexes, "other", tmp_path / "b")

        settings = replace(Settings(), cache_dir=str(indexes))
        assert bootstrap.catalog(settings).below(tmp_path / "a") == {}

    def test_finds_the_path_itself(self, tmp_path) -> None:
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        project.mkdir()
        self._make(indexes, "self", project)

        settings = replace(Settings(), cache_dir=str(indexes))
        assert set(bootstrap.catalog(settings).below(project)) == {project}

    def test_no_index_directory(self, tmp_path) -> None:
        from dataclasses import replace

        settings = replace(Settings(), cache_dir=str(tmp_path / "absent"))
        assert bootstrap.catalog(settings).below(tmp_path) == {}

    def test_a_file_without_a_root_is_skipped(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        indexes = tmp_path / "idx"
        indexes.mkdir()
        SqliteVectorStore(indexes / "anon.db", model_id="m").close()

        settings = replace(Settings(), cache_dir=str(indexes))
        assert bootstrap.catalog(settings).below(tmp_path) == {}


class TestFederatedWiring:
    """Verify which store the composition root hands to a search."""

    def _index_for(self, indexes, name, root):
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        SqliteVectorStore(indexes / f"{name}.db", model_id="m", root=root).close()

    def test_a_lone_tree_gets_a_plain_store(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        settings = replace(Settings(), cache_dir=str(tmp_path / "idx"))
        primary, reader = bootstrap.build_stores(settings, tmp_path, _StubEmbedder())
        try:
            assert isinstance(reader, SqliteVectorStore)
            assert primary is reader
        finally:
            reader.close()

    def test_a_parent_federates_over_its_children(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.federated import FederatedReader

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "one").mkdir(parents=True)
        self._index_for(indexes, "one", project / "one")

        settings = replace(Settings(), cache_dir=str(indexes))
        primary, reader = bootstrap.build_stores(settings, project, _StubEmbedder())
        try:
            assert isinstance(reader, FederatedReader)
            # No index covers the parent itself, so nothing is writable.
            assert primary is None
        finally:
            reader.close()

    def test_the_named_tree_stays_writable(self, tmp_path) -> None:
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "one").mkdir(parents=True)
        self._index_for(indexes, "one", project / "one")
        self._index_for(indexes, "root", project)

        from ish.adapters.vector_store.federated import FederatedReader

        settings = replace(Settings(), cache_dir=str(indexes))
        primary, reader = bootstrap.build_stores(settings, project, _StubEmbedder())
        try:
            assert primary is not None
            assert isinstance(reader, FederatedReader)
        finally:
            reader.close()

    def test_federation_can_be_turned_off(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "one").mkdir(parents=True)
        self._index_for(indexes, "one", project / "one")

        settings = replace(Settings(), cache_dir=str(indexes), federate=False)
        primary, reader = bootstrap.build_stores(settings, project, _StubEmbedder())
        try:
            assert isinstance(reader, SqliteVectorStore)
            assert primary is reader
        finally:
            reader.close()

    def _listings(self, monkeypatch) -> list[int]:
        """Count how often a store lists its chunks."""
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        listed: list[int] = []
        original = SqliteVectorStore.chunks

        def watched(store):
            listed.append(1)
            return original(store)

        monkeypatch.setattr(SqliteVectorStore, "chunks", watched)
        return listed

    def test_sibling_indexes_count_without_listing(self, tmp_path, monkeypatch) -> None:
        """A parent of siblings sums the counts, because siblings share no chunk."""
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        for name in ("one", "two"):
            (project / name).mkdir(parents=True)
            self._index_for(indexes, name, project / name)
        listed = self._listings(monkeypatch)

        settings = replace(Settings(), cache_dir=str(indexes))
        _primary, reader = bootstrap.build_stores(settings, project, _StubEmbedder())
        try:
            assert reader.count() == 0
            assert listed == []
        finally:
            reader.close()

    def test_nested_indexes_still_count_each_chunk_once(
        self, tmp_path, monkeypatch
    ) -> None:
        """A tree indexed inside another may repeat a chunk, so list to count."""
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "one").mkdir(parents=True)
        self._index_for(indexes, "one", project / "one")
        self._index_for(indexes, "root", project)
        listed = self._listings(monkeypatch)

        settings = replace(Settings(), cache_dir=str(indexes))
        _primary, reader = bootstrap.build_stores(settings, project, _StubEmbedder())
        try:
            assert reader.count() == 0
            assert listed == [1, 1]
        finally:
            reader.close()


class _StubBackend:
    """Stand in the registry for a backend, so forgetting needs no daemon."""

    model_name = "stub"

    @classmethod
    def from_option(cls, model: str, context_tokens=None) -> "_StubBackend":
        return cls()

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class TestForgetIndex:
    """Verify an index can be removed, and the shared store swept."""

    def _repo_with_two_trees(self, tmp_path):
        """Two indexed trees of one repository, one chunk each, one vector shared."""
        import subprocess
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore
        from ish.application.ports.vector_store import FileStamp
        from ish.domain.chunk import Chunk

        indexes = tmp_path / "idx"
        indexes.mkdir()
        shared = indexes / "vectors-shared.db"
        stores = {}
        (tmp_path / "repo").mkdir()
        subprocess.run(["git", "-C", str(tmp_path / "repo"), "init", "-q"], check=True)
        for name, hashes in (("one", ["common", "only-one"]), ("two", ["common"])):
            tree = tmp_path / "repo" / name
            tree.mkdir(parents=True)
            store = SqliteVectorStore(
                indexes / f"{name}.db",
                model_id="stub:",
                root=tree,
                repository=tmp_path / "repo" / ".git",
                checkout=tmp_path / "repo",
                shared=shared,
            )
            store.add_vectors({digest: [1.0, 0.0] for digest in hashes})
            store.set_file(
                tree / "a.py",
                FileStamp(mtime_ns=1, size=1),
                [
                    (
                        Chunk(
                            path=tree / "a.py",
                            text="",
                            kind="function",
                            language="python",
                            symbol=digest,
                            start_line=1,
                            end_line=2,
                        ),
                        digest,
                    )
                    for digest in hashes
                ],
            )
            store.close()
            stores[name] = indexes / f"{name}.db"
        settings = replace(Settings(), cache_dir=str(indexes))
        return settings, stores, shared

    def _hashes(self, shared) -> set[str]:
        import sqlite3

        db = sqlite3.connect(shared)
        try:
            return {r[0] for r in db.execute("SELECT content_hash FROM vectors")}
        finally:
            db.close()

    def test_the_index_goes_and_the_vectors_stay(self, tmp_path, caplog) -> None:
        """A checkout at an older commit still needs what no index names."""
        import logging

        settings, stores, shared = self._repo_with_two_trees(tmp_path)
        assert self._hashes(shared) == {"common", "only-one"}

        with caplog.at_level(logging.INFO, logger="ish.bootstrap"):
            removed = bootstrap.forget_index(settings, tmp_path / "repo" / "one")
        assert stores["one"] in removed
        assert not stores["one"].exists()
        assert stores["two"].exists()
        assert self._hashes(shared) == {"common", "only-one"}
        assert any("--sweep" in r.getMessage() for r in caplog.records)

    def test_a_sweep_takes_what_no_index_names(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", _StubBackend)
        settings, stores, shared = self._repo_with_two_trees(tmp_path)
        bootstrap.forget_index(settings, tmp_path / "repo" / "one")

        assert bootstrap.sweep_vectors(settings, tmp_path / "repo" / "two") == 1
        assert self._hashes(shared) == {"common"}

    def test_a_sweep_spares_what_any_index_names(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", _StubBackend)
        settings, _stores, shared = self._repo_with_two_trees(tmp_path)
        assert bootstrap.sweep_vectors(settings, tmp_path / "repo") == 0
        assert self._hashes(shared) == {"common", "only-one"}

    def test_a_sweep_outside_a_repository_takes_the_trees_own(
        self, tmp_path, monkeypatch
    ) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", _StubBackend)
        indexes = tmp_path / "idx"
        tree = tmp_path / "alone"
        tree.mkdir()
        store = SqliteVectorStore(indexes / "alone.db", model_id="m", root=tree)
        store.add_vectors({"orphan": [1.0, 0.0]})
        store.close()
        settings = replace(Settings(), cache_dir=str(indexes))
        assert bootstrap.sweep_vectors(settings, tree) == 1

    def test_a_sweep_with_nothing_to_ask_is_an_error(self, tmp_path) -> None:
        import subprocess
        from dataclasses import replace

        settings = replace(Settings(), cache_dir=str(tmp_path / "idx"))
        alone = tmp_path / "alone"
        alone.mkdir()
        with pytest.raises(LookupError, match="in no repository"):
            bootstrap.sweep_vectors(settings, alone)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        with pytest.raises(LookupError, match="belongs to the repository"):
            bootstrap.sweep_vectors(settings, repo)

    def test_a_gone_tree_can_be_forgotten(self, tmp_path) -> None:
        """The tree is usually gone. That is the reason to forget its index."""
        import shutil

        settings, stores, _shared = self._repo_with_two_trees(tmp_path)
        shutil.rmtree(tmp_path / "repo" / "one")
        removed = bootstrap.forget_index(settings, tmp_path / "repo" / "one")
        assert stores["one"] in removed
        assert not stores["one"].exists()

    def test_an_unknown_tree_is_an_error(self, tmp_path) -> None:
        from dataclasses import replace

        settings = replace(Settings(), cache_dir=str(tmp_path / "idx"))
        with pytest.raises(LookupError, match="No stored index"):
            bootstrap.forget_index(settings, tmp_path / "nowhere")

    def test_a_tree_outside_a_repository_just_goes(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        indexes = tmp_path / "idx"
        tree = tmp_path / "alone"
        tree.mkdir()
        SqliteVectorStore(indexes / "alone.db", model_id="m", root=tree).close()
        settings = replace(Settings(), cache_dir=str(indexes))
        removed = bootstrap.forget_index(settings, tree)
        assert indexes / "alone.db" in removed
        assert list(indexes.glob("*.db")) == []


class TestBusyIndexes:
    """Verify that an index under a writer does not stop a search above it.

    An index run holds its own index for the length of the run. A
    parent reads several, so it must answer from the rest and say
    which one it left out.
    """

    def _project(self, tmp_path, names):
        """Index each child of a project and return the file holding each."""
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        settings = replace(Settings(), cache_dir=str(indexes))
        catalog = bootstrap.catalog(settings)
        held = {}
        for name in names:
            tree = project / name
            tree.mkdir(parents=True)
            # The catalog names the file for a tree, so the index the
            # composition root opens is the one this test holds.
            db = catalog.path_for(tree.resolve())
            SqliteVectorStore(db, model_id="m", root=tree.resolve()).close()
            held[name] = db
        return settings, project, held

    def _hold(self, monkeypatch, busy):
        """Make opening each index in *busy* report a writer."""
        from ish.adapters.vector_store.sqlite import SqliteVectorStore
        from ish.application.ports.vector_store import StoreBusy

        def open_or_report(path, *, model_id, root, **identity):
            if path in busy:
                raise StoreBusy(f"Another process holds the index {path}.")
            return SqliteVectorStore(path, model_id=model_id, root=root, **identity)

        monkeypatch.setattr(
            "ish.adapters.vector_store.sqlite.SqliteVectorStore", open_or_report
        )

    def test_a_held_index_is_left_out_and_named(self, tmp_path, monkeypatch, caplog):
        settings, project, held = self._project(tmp_path, ["one", "two"])
        self._hold(monkeypatch, {held["one"]})

        with caplog.at_level("WARNING"):
            primary, reader = bootstrap.build_stores(settings, project, _StubEmbedder())
        try:
            assert primary is None
            assert len(reader._readers) == 1
            assert str(held["one"]) in caplog.text
        finally:
            reader.close()

    def test_every_index_held_is_an_error(self, tmp_path, monkeypatch):
        """Nothing is left to read, so say so rather than answer nothing."""
        from ish.application.ports.vector_store import StoreBusy

        settings, project, held = self._project(tmp_path, ["one"])
        self._hold(monkeypatch, set(held.values()))

        with pytest.raises(StoreBusy, match="every index"):
            bootstrap.build_stores(settings, project, _StubEmbedder())

    def test_the_named_tree_reports_its_own_writer(self, tmp_path, monkeypatch):
        """The caller asked about this tree, so silence would be wrong."""
        from ish.application.ports.vector_store import StoreBusy

        settings, project, held = self._project(tmp_path, ["one"])
        self._hold(monkeypatch, set(held.values()))

        with pytest.raises(StoreBusy, match="holds the index"):
            bootstrap.build_stores(settings, project / "one", _StubEmbedder())


class TestRefreshIndexes:
    """Verify that refreshing a parent visits every index beneath it."""

    @pytest.fixture()
    def offline(self, monkeypatch):
        class Fake:
            model_name = "fake"

            def embed_documents(self, texts):
                return [[float(len(t)), 1.0] for t in texts]

            def embed_query(self, text):
                return [float(len(text)), 1.0]

        monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())

    @pytest.fixture()
    def nested(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        root = tmp_path / "proj"
        (root / "a").mkdir(parents=True)
        (root / "b").mkdir(parents=True)
        (root / "a" / "one.py").write_text("def one():\n    pass\n")
        (root / "b" / "two.py").write_text("def two():\n    pass\n")
        return root

    def _settings(self) -> Settings:
        return Settings(git=False)

    def test_refreshes_each_index_below(self, nested: Path, offline) -> None:
        settings = self._settings()
        # Build one index per subdirectory, the way naming each does.
        for sub in ("a", "b"):
            search = bootstrap.build_search(
                replace(settings, federate=False), nested / sub
            )
            search.build_index(nested / sub)
            search.close()

        refreshed = bootstrap.refresh_indexes(settings, nested)
        assert refreshed == [nested / "a", nested / "b"]

    def test_a_refresh_picks_up_a_new_file(self, nested: Path, offline) -> None:
        settings = self._settings()
        search = bootstrap.build_search(replace(settings, federate=False), nested / "a")
        search.build_index(nested / "a")
        search.close()

        (nested / "a" / "three.py").write_text("def three():\n    pass\n")
        bootstrap.refresh_indexes(settings, nested)

        after = bootstrap.build_search(replace(settings, federate=False), nested / "a")
        symbols = {c.symbol for c in after.all_chunks()}
        after.close()
        assert "three" in symbols

    def test_a_tree_with_no_index_refreshes_itself(self, nested: Path, offline) -> None:
        """Naming a tree that has none builds one, rather than doing nothing."""
        assert bootstrap.refresh_indexes(self._settings(), nested) == [nested]

    def test_refreshing_does_not_recurse(self, nested: Path, offline) -> None:
        """Each child writes to its own index, so federation must be off."""
        settings = self._settings()
        bootstrap.refresh_indexes(settings, nested / "a")
        found = bootstrap.catalog(settings).below(nested)
        assert list(found) == [nested / "a"]


class TestRefreshReadsEachTreeConfig:
    """Verify a refresh honours the configuration beside each tree.

    An index-scope option decides what belongs in an index. Refreshing a
    tree under its parent's options would prune everything those options
    reject, which would empty an index that a local setting keeps alive.
    """

    @pytest.fixture()
    def offline(self, monkeypatch):
        class Fake:
            model_name = "fake"

            def embed_documents(self, texts):
                return [[float(len(t)), 1.0] for t in texts]

            def embed_query(self, text):
                return [float(len(text)), 1.0]

        monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())

    @pytest.fixture()
    def nested(self, tmp_path: Path, monkeypatch) -> Path:
        """Build a child that git hides, kept by a config of its own.

        This is the shape that matters: a working copy of another version
        control system inside a git repository, which git reports nothing
        for.
        """
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        root = tmp_path / "proj"
        child = root / "child"
        child.mkdir(parents=True)
        (child / "mod.py").write_text("def kept():\n    pass\n")
        # Git shows tracked files and untracked ones no rule covers, so
        # the child has to be ignored for the parent to reject it.
        (root / ".gitignore").write_text("child/\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (child / CONFIG_DIRNAME).mkdir()
        (child / CONFIG_DIRNAME / CONFIG_BASENAME).write_text("git = false\n")
        return root

    def _index_child(self, root: Path) -> int:
        child = root / "child"
        settings = replace(load_settings(start=child, environ={}), federate=False)
        search = bootstrap.build_search(settings, child)
        try:
            search.build_index(child)
            return len(search.all_chunks())
        finally:
            search.close()

    def test_git_hides_the_child_from_the_parent(self, nested: Path) -> None:
        """Confirm the setup: the parent's options reject every file."""
        parent = load_settings(start=nested, environ={})
        scan = bootstrap.build_scan(parent, nested)
        assert not scan.accepts(nested / "child" / "mod.py")

    def test_the_child_index_survives_a_refresh_from_the_parent(
        self, nested: Path, offline
    ) -> None:
        assert self._index_child(nested) > 0

        parent = load_settings(start=nested, environ={})
        bootstrap.refresh_indexes(parent, nested)

        search = bootstrap.build_search(
            replace(parent, federate=False), nested / "child"
        )
        try:
            assert search.all_chunks(), "the refresh pruned the child index"
        finally:
            search.close()

    def test_a_flag_still_overrides_the_local_file(self, nested: Path, offline) -> None:
        """A flag beats a config file, for the tree as for the parent."""
        self._index_child(nested)
        parent = load_settings(start=nested, environ={})
        bootstrap.refresh_indexes(parent, nested, overrides={"git": True})

        search = bootstrap.build_search(
            replace(parent, federate=False), nested / "child"
        )
        try:
            assert not search.all_chunks()
        finally:
            search.close()


class TestConfigLocation:
    """Verify where a project configuration is read from."""

    def test_the_directory_form_is_preferred(self, tmp_path: Path) -> None:
        (tmp_path / CONFIG_DIRNAME).mkdir()
        (tmp_path / CONFIG_DIRNAME / CONFIG_BASENAME).write_text("limit = 5\n")
        (tmp_path / CONFIG_FILENAME).write_text("limit = 9\n")
        assert load_settings(start=tmp_path, environ={}).limit == 5

    def test_the_flat_form_still_works(self, tmp_path: Path) -> None:
        (tmp_path / CONFIG_FILENAME).write_text("limit = 9\n")
        assert load_settings(start=tmp_path, environ={}).limit == 9

    def test_found_by_walking_upward(self, tmp_path: Path) -> None:
        (tmp_path / CONFIG_DIRNAME).mkdir()
        (tmp_path / CONFIG_DIRNAME / CONFIG_BASENAME).write_text("limit = 7\n")
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        assert load_settings(start=deep, environ={}).limit == 7


class TestFederationWarning:
    """Verify what a read-only parent search reports."""

    @pytest.fixture()
    def nested(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        root = tmp_path / "proj"
        (root / "child").mkdir(parents=True)
        (root / "child" / "mod.py").write_text("def one():\n    pass\n")
        return root

    class _Fake:
        model_name = "fake"

        def embed_documents(self, texts):
            return [[float(len(t)), 1.0] for t in texts]

        def embed_query(self, text):
            return [float(len(text)), 1.0]

    def _seed_child(self, root: Path) -> Settings:
        settings = Settings(git=False)
        search = bootstrap.build_search(
            replace(settings, federate=False), root / "child"
        )
        search.build_index(root / "child")
        search.close()
        return settings

    def test_a_read_only_parent_warns(self, nested: Path, monkeypatch, caplog):
        monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: self._Fake())
        settings = self._seed_child(nested)
        with caplog.at_level("WARNING"):
            bootstrap.build_search(settings, nested).close()
        assert "without refreshing" in caplog.text

    def test_a_refresh_silences_the_warning(self, nested: Path, monkeypatch, caplog):
        """The warning asks for --refresh, so it must not follow one."""
        monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: self._Fake())
        settings = replace(self._seed_child(nested), refresh=True)
        with caplog.at_level("WARNING"):
            bootstrap.build_search(settings, nested).close()
        assert "without refreshing" not in caplog.text

    def test_a_kept_parent_stays_quiet(self, nested: Path, monkeypatch, caplog):
        """ish-serve keeps the indexes current, so there is nothing to ask for."""
        monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: self._Fake())
        settings = self._seed_child(nested)
        with caplog.at_level("WARNING"):
            bootstrap.build_search(settings, nested, kept=True).close()
        assert "without refreshing" not in caplog.text


class TestCoveringIndex:
    """Verify that asking inside an indexed tree reads what is there.

    Building a second index for a subdirectory would embed every file
    again, because a vector is shared only within one index file.
    """

    @pytest.fixture()
    def offline(self, monkeypatch):
        class Fake:
            model_name = "fake"

            def embed_documents(self, texts):
                return [[float(len(t)), 1.0] for t in texts]

            def embed_query(self, text):
                return [float(len(text)), 1.0]

        monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())

    @pytest.fixture()
    def tree(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        root = tmp_path / "proj"
        (root / "inner").mkdir(parents=True)
        (root / "outer.py").write_text("def outer():\n    pass\n")
        (root / "inner" / "deep.py").write_text("def deep():\n    pass\n")
        return root

    def _index(self, path: Path) -> None:
        settings = Settings(git=False)
        search = bootstrap.build_search(replace(settings, federate=False), path)
        search.build_index(path)
        search.close()

    def test_a_covering_index_is_found(self, tree: Path, offline) -> None:
        self._index(tree)
        found = bootstrap.catalog(Settings()).covering(tree / "inner")
        assert found is not None and found[0] == tree

    def test_nothing_covers_the_tree_itself(self, tree: Path, offline) -> None:
        self._index(tree)
        assert bootstrap.catalog(Settings()).covering(tree) is None

    def test_no_second_index_is_created(self, tree: Path, offline) -> None:
        self._index(tree)
        before = sorted(bootstrap.index_dir(Settings()).glob("*.db"))
        search = bootstrap.build_search(Settings(git=False), tree / "inner")
        search.build_index(tree / "inner")
        search.close()
        assert sorted(bootstrap.index_dir(Settings()).glob("*.db")) == before

    def test_nothing_is_embedded_again(self, tree: Path, offline) -> None:
        self._index(tree)
        counted = {"n": 0}

        class Counting:
            model_name = "fake"

            def embed_documents(self, texts):
                counted["n"] += len(texts)
                return [[float(len(t)), 1.0] for t in texts]

            def embed_query(self, text):
                return [float(len(text)), 1.0]

        original = bootstrap.build_embedder
        bootstrap.build_embedder = lambda settings: Counting()
        try:
            search = bootstrap.build_search(Settings(git=False), tree / "inner")
            search.build_index(tree / "inner")
            search.close()
        finally:
            bootstrap.build_embedder = original
        assert counted["n"] == 0

    def test_the_answers_stay_inside_the_path(self, tree: Path, offline) -> None:
        self._index(tree)
        search = bootstrap.build_search(Settings(git=False), tree / "inner")
        try:
            chunks = search.all_chunks()
        finally:
            search.close()
        assert chunks
        assert all((tree / "inner") in c.path.parents for c in chunks)

    def test_the_parent_still_sees_everything(self, tree: Path, offline) -> None:
        """Reading through a child must not narrow the parent."""
        self._index(tree)
        search = bootstrap.build_search(Settings(git=False), tree / "inner")
        search.build_index(tree / "inner")
        search.close()

        parent = bootstrap.build_search(Settings(git=False), tree)
        try:
            symbols = {c.symbol for c in parent.all_chunks()}
        finally:
            parent.close()
        assert symbols == {"outer", "deep"}
