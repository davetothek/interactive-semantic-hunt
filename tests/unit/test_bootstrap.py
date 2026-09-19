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
