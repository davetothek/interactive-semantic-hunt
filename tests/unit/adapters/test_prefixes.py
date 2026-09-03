"""Test the task-prefix table shared by the embedding adapters."""

import pytest

from ish.adapters.embedder.prefixes import PrefixingEmbedder, prefixes_for


class TestPrefixTable:
    """Verify which models carry a convention."""

    @pytest.mark.parametrize(
        "model",
        ["nomic-embed-text", "nomic-embed-text:latest", "NOMIC-EMBED-TEXT"],
    )
    def test_nomic_variants_match(self, model: str) -> None:
        assert prefixes_for(model) == ("search_document: ", "search_query: ")

    def test_gguf_repository_name_matches(self) -> None:
        """The llama.cpp adapter names the model by repository and file."""
        document, query = prefixes_for(
            "nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.Q4_K_M.gguf"
        )
        assert document == "search_document: "
        assert query == "search_query: "

    def test_mxbai_prefixes_the_query_only(self) -> None:
        document, query = prefixes_for("mxbai-embed-large")
        assert document == ""
        assert query.startswith("Represent this sentence")

    def test_unknown_model_gets_nothing(self) -> None:
        assert prefixes_for("all-MiniLM-L6-v2") == ("", "")

    def test_empty_name_gets_nothing(self) -> None:
        assert prefixes_for("") == ("", "")


class Spy(PrefixingEmbedder):
    """Record the text that reaches the backend."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.seen: list[str] = []

    def _embed(self, texts):
        self.seen.extend(texts)
        return [[1.0] for _ in texts]


class TestPrefixingEmbedder:
    """Verify the base class applies the right prefix on each side."""

    def test_documents_get_the_document_prefix(self) -> None:
        spy = Spy("nomic-embed-text")
        spy.embed_documents(["one", "two"])
        assert spy.seen == ["search_document: one", "search_document: two"]

    def test_query_gets_the_query_prefix(self) -> None:
        spy = Spy("nomic-embed-text")
        spy.embed_query("find it")
        assert spy.seen == ["search_query: find it"]

    def test_no_convention_leaves_text_alone(self) -> None:
        spy = Spy("all-MiniLM-L6-v2")
        spy.embed_documents(["one"])
        spy.embed_query("two")
        assert spy.seen == ["one", "two"]

    def test_empty_documents_skip_the_backend(self) -> None:
        spy = Spy("nomic-embed-text")
        assert spy.embed_documents([]) == []
        assert spy.seen == []

    def test_query_returns_a_single_vector(self) -> None:
        assert Spy("nomic-embed-text").embed_query("q") == [1.0]

    def test_query_handles_an_empty_reply(self) -> None:
        class Silent(Spy):
            def _embed(self, texts):
                return []

        assert Silent("nomic-embed-text").embed_query("q") == []

    def test_base_class_requires_an_implementation(self) -> None:
        with pytest.raises(NotImplementedError):
            PrefixingEmbedder()._embed(["x"])
