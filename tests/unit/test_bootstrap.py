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


def test_build_scan_wires_the_use_case() -> None:
    assert isinstance(bootstrap.build_scan(Settings()), Scan)


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
