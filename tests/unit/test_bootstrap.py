"""Test the composition root."""

import pytest

from ish import bootstrap
from ish.application.ports.parser import Parser
from ish.application.scan import Scan
from ish.settings import Settings


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
