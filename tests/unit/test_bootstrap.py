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
        monkeypatch.setattr(
            "ish.adapters.embedder.llama_cpp.LlamaCppEmbedder", fake
        )
        bootstrap.EMBEDDERS["llama.cpp"]("org/repo-GGUF/weights.gguf")
        fake.assert_called_once_with(
            repo_id="org/repo-GGUF", filename="weights.gguf"
        )

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
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
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
