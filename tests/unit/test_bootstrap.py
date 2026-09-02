"""Test the composition root."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ish import bootstrap
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


class TestModelOverride:
    """Verify that the model setting reaches each backend."""

    def test_llama_cpp_splits_repo_and_file(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        fake = MagicMock()
        monkeypatch.setattr("ish.adapters.embedder.llama_cpp.LlamaCppEmbedder", fake)
        bootstrap.EMBEDDERS["llama.cpp"]("org/repo-GGUF/weights.gguf")
        fake.assert_called_once_with(repo_id="org/repo-GGUF", filename="weights.gguf")

    def test_ollama_passes_model_name(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        fake = MagicMock()
        monkeypatch.setattr("ish.adapters.embedder.ollama.OllamaEmbedder", fake)
        bootstrap.EMBEDDERS["ollama"]("mxbai-embed-large")
        fake.assert_called_once_with(model_name="mxbai-embed-large")

    def test_sentence_transformer_passes_model_name(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        fake = MagicMock()
        monkeypatch.setattr(
            "ish.adapters.embedder.sentence_transformer.SentenceTransformerEmbedder",
            fake,
        )
        bootstrap.EMBEDDERS["st"]("all-mpnet-base-v2")
        fake.assert_called_once_with(model_name="all-mpnet-base-v2")


class TestLanguageSelection:
    """Verify that the languages option chooses which parsers are built."""

    def test_empty_enables_every_parser(self) -> None:
        from dataclasses import replace

        built = bootstrap.build_parsers(replace(Settings(), languages=()))
        assert {p.language for p in built} == set(bootstrap.PARSERS)

    def test_named_language_is_the_only_one_built(self) -> None:
        from dataclasses import replace

        built = bootstrap.build_parsers(replace(Settings(), languages=("python",)))
        assert [p.language for p in built] == ["python"]

    def test_unknown_language_is_reported(self) -> None:
        from dataclasses import replace

        with pytest.raises(ValueError, match="Unknown language"):
            bootstrap.build_parsers(replace(Settings(), languages=("cobol",)))

    def test_every_registered_parser_satisfies_the_port(self) -> None:
        """Guard the registry itself, so a new entry cannot be malformed."""
        for name, factory in bootstrap.PARSERS.items():
            parser = factory()
            assert isinstance(parser, Parser), name
            assert parser.language == name, name
            assert parser.suffixes, name
            assert all(s.startswith(".") for s in parser.suffixes), name

    def test_registered_parsers_claim_distinct_suffixes(self) -> None:
        """The default registry must build without a suffix conflict."""
        seen: dict[str, str] = {}
        for name, factory in bootstrap.PARSERS.items():
            for suffix in factory().suffixes:
                assert suffix not in seen, f"{name} and {seen[suffix]} share {suffix}"
                seen[suffix] = name


class TestVectorStoreWiring:
    """Verify how the store is chosen and where the index lands."""

    def test_no_cache_uses_the_in_memory_store(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.pure_python import PurePythonVectorStore

        settings = replace(Settings(), no_cache=True)
        store = bootstrap.build_vector_store(settings, tmp_path, _StubEmbedder())
        try:
            assert isinstance(store, PurePythonVectorStore)
        finally:
            store.close()

    def test_default_persists_to_sqlite(self, tmp_path, monkeypatch) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        settings = replace(Settings(), cache_dir=str(tmp_path / "idx"))
        store = bootstrap.build_vector_store(settings, tmp_path, _StubEmbedder())
        try:
            assert isinstance(store, SqliteVectorStore)
        finally:
            store.close()
        assert list((tmp_path / "idx").glob("*.db"))

    def test_cache_dir_option_wins(self, tmp_path) -> None:
        from dataclasses import replace

        settings = replace(Settings(), cache_dir=str(tmp_path / "here"))
        assert bootstrap.index_dir(settings) == tmp_path / "here"

    def test_default_dir_honors_xdg(self, tmp_path, monkeypatch) -> None:
        """The index is data, not cache, so a cleanup cannot discard it."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert bootstrap.index_dir(Settings()) == tmp_path / "ish"

    def test_index_path_separates_projects(self, tmp_path) -> None:
        """Two trees must never share one index file."""
        a = bootstrap.index_path(Settings(), tmp_path / "one")
        b = bootstrap.index_path(Settings(), tmp_path / "two")
        assert a != b
        assert a.name.startswith("one-")
        assert b.name.startswith("two-")

    def test_index_path_is_stable(self, tmp_path) -> None:
        assert bootstrap.index_path(Settings(), tmp_path) == bootstrap.index_path(
            Settings(), tmp_path
        )

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


class TestBackendDefaults:
    """Verify each factory falls back to its own default model."""

    def test_llama_cpp_without_a_model(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        fake = MagicMock()
        monkeypatch.setattr("ish.adapters.embedder.llama_cpp.LlamaCppEmbedder", fake)
        bootstrap.EMBEDDERS["llama.cpp"]("")
        fake.assert_called_once_with()

    def test_ollama_without_a_model(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        fake = MagicMock()
        monkeypatch.setattr("ish.adapters.embedder.ollama.OllamaEmbedder", fake)
        bootstrap.EMBEDDERS["ollama"]("")
        fake.assert_called_once_with()

    def test_sentence_transformer_without_a_model(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        fake = MagicMock()
        monkeypatch.setattr(
            "ish.adapters.embedder.sentence_transformer.SentenceTransformerEmbedder",
            fake,
        )
        bootstrap.EMBEDDERS["st"]("")
        fake.assert_called_once_with()

    def test_ollama_is_the_default_backend(self) -> None:
        """The default must need no model load per process."""
        assert Settings().embedder == "ollama"


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
        found = bootstrap.find_indexes(settings, project)
        assert set(found) == {project / "sub"}

    def test_ignores_an_unrelated_tree(self, tmp_path) -> None:
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        self._make(indexes, "other", tmp_path / "b")

        settings = replace(Settings(), cache_dir=str(indexes))
        assert bootstrap.find_indexes(settings, tmp_path / "a") == {}

    def test_finds_the_path_itself(self, tmp_path) -> None:
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        project.mkdir()
        self._make(indexes, "self", project)

        settings = replace(Settings(), cache_dir=str(indexes))
        assert set(bootstrap.find_indexes(settings, project)) == {project}

    def test_no_index_directory(self, tmp_path) -> None:
        from dataclasses import replace

        settings = replace(Settings(), cache_dir=str(tmp_path / "absent"))
        assert bootstrap.find_indexes(settings, tmp_path) == {}

    def test_a_file_without_a_root_is_skipped(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        indexes = tmp_path / "idx"
        indexes.mkdir()
        SqliteVectorStore(indexes / "anon.db", model_id="m").close()

        settings = replace(Settings(), cache_dir=str(indexes))
        assert bootstrap.find_indexes(settings, tmp_path) == {}


class TestFederatedWiring:
    """Verify which store the composition root hands to a search."""

    def _index_for(self, indexes, name, root):
        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        SqliteVectorStore(indexes / f"{name}.db", model_id="m", root=root).close()

    def test_a_lone_tree_gets_a_plain_store(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        settings = replace(Settings(), cache_dir=str(tmp_path / "idx"))
        store = bootstrap.build_vector_store(settings, tmp_path, _StubEmbedder())
        try:
            assert isinstance(store, SqliteVectorStore)
        finally:
            store.close()

    def test_a_parent_federates_over_its_children(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.federated import FederatedVectorStore

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "one").mkdir(parents=True)
        self._index_for(indexes, "one", project / "one")

        settings = replace(Settings(), cache_dir=str(indexes))
        store = bootstrap.build_vector_store(settings, project, _StubEmbedder())
        try:
            assert isinstance(store, FederatedVectorStore)
            # No index covers the parent itself, so nothing is writable.
            assert store.writable is False
        finally:
            store.close()

    def test_the_named_tree_stays_writable(self, tmp_path) -> None:
        from dataclasses import replace

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "one").mkdir(parents=True)
        self._index_for(indexes, "one", project / "one")
        self._index_for(indexes, "root", project)

        settings = replace(Settings(), cache_dir=str(indexes))
        store = bootstrap.build_vector_store(settings, project, _StubEmbedder())
        try:
            assert store.writable is True
        finally:
            store.close()

    def test_federation_can_be_turned_off(self, tmp_path) -> None:
        from dataclasses import replace

        from ish.adapters.vector_store.sqlite import SqliteVectorStore

        indexes = tmp_path / "idx"
        indexes.mkdir()
        project = tmp_path / "proj"
        (project / "one").mkdir(parents=True)
        self._index_for(indexes, "one", project / "one")

        settings = replace(Settings(), cache_dir=str(indexes), federate=False)
        store = bootstrap.build_vector_store(settings, project, _StubEmbedder())
        try:
            assert isinstance(store, SqliteVectorStore)
        finally:
            store.close()


class TestRefreshIndexes:
    """Verify that refreshing a parent visits every index beneath it."""

    @pytest.fixture()
    def offline(self, monkeypatch):
        class Fake:
            model_id = "fake"

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
        found = bootstrap.find_indexes(settings, nested)
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
            model_id = "fake"

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
