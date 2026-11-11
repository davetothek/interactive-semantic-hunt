"""Test the MCP chunk source against a real ish server on a free port."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from ish import bootstrap
from ish.adapters.chunk_source.mcp import McpChunkSource
from ish.application.ports.chunk_source import ChunkSource, ChunkSourceError
from ish.interfaces.mcp import server as server_module
from ish.interfaces.mcp.protocol import Tool
from ish.settings import MCP_TOKEN_ENV_VAR, Settings
from tests.unit.interfaces.mcp.test_mcp_http import TOKEN, Running


class Embedder:
    model_name = "fake"

    def embed_documents(self, texts):
        return [[float(len(text)), 1.0] for text in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


@pytest.fixture(scope="module")
def served(tmp_path_factory) -> Iterator[Running]:
    # One server for the module. Stopping one waits out its poll interval.
    tree = tmp_path_factory.mktemp("tree")
    (tree / "exposure.md").write_text(
        "# Exposure\n\nThe Auto Exposure (AE) block runs before the gain.\n"
    )
    settings = replace(
        Settings(),
        no_cache=True,
        git=False,
        cache_dir=str(tmp_path_factory.mktemp("idx")),
        link_base="https://docs.example.invalid/tree",
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bootstrap, "build_embedder", lambda s: Embedder())
        tools = server_module.IshTools(settings, tree)
        made = Running(tools.tools(over_http=True))
        yield made
        made.close()
        tools.close()


def _source(running: Running) -> McpChunkSource:
    return McpChunkSource(f"{running.url}/mcp", TOKEN, MCP_TOKEN_ENV_VAR, timeout=10)


def _serving(name: str, answer) -> Running:
    return Running([Tool(name, "Answer.", {"type": "object"}, answer)])


def _arguments(running: Running) -> dict:
    return json.loads(running.calls[-1])["params"]["arguments"]


class TestPort:
    def test_the_adapter_satisfies_the_port(self, served: Running) -> None:
        assert isinstance(_source(served), ChunkSource)

    def test_the_host_names_the_server(self, served: Running) -> None:
        assert _source(served).host == served.url.removeprefix("http://")

    def test_the_version_is_empty_before_the_first_call(self, served: Running) -> None:
        assert _source(served).version == ""

    def test_the_version_is_what_the_server_reports(self, served: Running) -> None:
        source = _source(served)
        source.search("exposure", 5, "plain")
        assert source.version == "0"

    def test_an_empty_token_names_the_variable(self, served: Running) -> None:
        with pytest.raises(ChunkSourceError, match=MCP_TOKEN_ENV_VAR):
            McpChunkSource(f"{served.url}/mcp", "", MCP_TOKEN_ENV_VAR)


class TestSearch:
    def test_the_lines_are_the_lines_of_the_server(self, served: Running) -> None:
        text = _source(served).search("exposure", 5, "plain")
        (line,) = text.splitlines()
        assert line.endswith("exposure.md:1-3  document  Exposure")

    def test_the_shape_reaches_the_server(self, served: Running) -> None:
        text = _source(served).search("exposure", 5, "grep")
        (line,) = text.splitlines()
        assert line.startswith("exposure.md:1:")

    def test_a_search_that_finds_nothing_returns_no_text(self, served: Running) -> None:
        assert _source(served).search("exposure", 5, "plain", lang=["python"]) == ""

    def test_a_result_that_quotes_the_marker_is_kept(self) -> None:
        line = "notes.md:1-2  section  No results for 'x' under review"
        running = _serving("search_code", lambda arguments: line)
        try:
            assert _source(running).search("x", 5, "plain") == line
        finally:
            running.close()

    def test_only_the_filters_that_are_set_travel(self, served: Running) -> None:
        source = _source(served)
        source.search("exposure", 5, "plain")
        assert _arguments(served) == {
            "query": "exposure",
            "limit": 5,
            "format": "plain",
        }
        source.search("exposure", 5, "plain", lang=["markdown"], type=["doc"])
        assert _arguments(served) == {
            "query": "exposure",
            "limit": 5,
            "format": "plain",
            "lang": ["markdown"],
            "type": ["doc"],
        }

    def test_under_travels(self) -> None:
        running = _serving("search_code", lambda arguments: "a.md:1-2  section  A")
        try:
            _source(running).search("x", 5, "plain", under="docs")
            assert _arguments(running)["under"] == "docs"
        finally:
            running.close()


class TestSources:
    def test_a_chunk_arrives_with_its_text_and_score(self, served: Running) -> None:
        found = _source(served).sources("exposure", 5)
        (entry,) = found.chunks
        assert entry.chunk.path == Path("exposure.md")
        assert (entry.chunk.kind, entry.chunk.language) == ("document", "markdown")
        assert entry.chunk.symbol == "Exposure"
        assert (entry.chunk.start_line, entry.chunk.end_line) == (1, 3)
        assert "The Auto Exposure (AE) block runs before the gain." in entry.text
        assert isinstance(entry.score, float)

    def test_the_chunk_itself_holds_no_text(self, served: Running) -> None:
        (entry,) = _source(served).sources("exposure", 5).chunks
        assert entry.chunk.text == ""

    def test_a_term_arrives_with_the_chunk_that_spells_it_out(
        self, served: Running
    ) -> None:
        found = _source(served).sources("exposure", 5)
        (term,) = found.terms
        assert (term.short, term.meaning) == ("AE", "Auto Exposure")
        assert term.chunk == found.chunks[0].chunk

    def test_the_link_and_the_type_are_those_of_the_server(
        self, served: Running
    ) -> None:
        source = _source(served)
        (entry,) = source.sources("exposure", 5).chunks
        assert source.link(entry.chunk) == (
            "https://docs.example.invalid/tree/exposure.md#L1-3"
        )
        assert source.kind_of(entry.chunk) == "doc"

    def test_a_chunk_the_server_never_sent_has_no_link_and_no_type(
        self, served: Running
    ) -> None:
        source = _source(served)
        (entry,) = source.sources("exposure", 5).chunks
        other = replace(entry.chunk, path=Path("other.md"))
        assert source.link(other) == ""
        assert source.kind_of(other) == ""

    def test_the_filters_travel(self, served: Running) -> None:
        _source(served).sources("exposure", 7, lang=["markdown"], under=".")
        assert _arguments(served) == {
            "query": "exposure",
            "limit": 7,
            "lang": ["markdown"],
            "under": ".",
        }

    def test_a_chunk_with_no_symbol_keeps_none(self) -> None:
        reply = {
            "sources": [
                {
                    "path": "a.yaml",
                    "type": "config",
                    "location": {"kind": "lines", "start": 2, "end": 4},
                    "kind": "document",
                    "symbol": None,
                    "language": "yaml",
                    "url": "",
                    "score": 0.5,
                    "text": "key: value",
                }
            ],
            "terms": [],
        }
        running = _serving("sources", lambda arguments: json.dumps(reply))
        try:
            (entry,) = _source(running).sources("key", 5).chunks
            assert entry.chunk.symbol is None
        finally:
            running.close()


class TestServerWithoutTheTool:
    """An older server has no sources tool, and the client says what to do."""

    def test_the_error_tells_the_user_to_update_the_server(self) -> None:
        running = _serving("search_code", lambda arguments: "")
        try:
            source = _source(running)
            with pytest.raises(ChunkSourceError) as failure:
                source.sources("exposure", 5)
            assert str(failure.value) == (
                f"{source.host} has no sources tool. Update ish on the server."
            )
        finally:
            running.close()

    def test_another_failure_of_the_tool_keeps_its_message(self) -> None:
        def refuse(arguments: dict) -> str:
            raise ValueError("'limit' must be from 1 to 200.")

        running = _serving("sources", refuse)
        try:
            with pytest.raises(ChunkSourceError, match="from 1 to 200"):
                _source(running).sources("exposure", 500)
        finally:
            running.close()


class TestUnreadableAnswer:
    @pytest.mark.parametrize(
        "answer",
        [
            "not json",
            "[]",
            json.dumps({"sources": []}),
            json.dumps({"sources": [{"path": "a.md"}], "terms": []}),
            json.dumps({"sources": [], "terms": [{"term": "AE"}]}),
            json.dumps({"sources": 3, "terms": []}),
        ],
    )
    def test_an_answer_in_another_shape_names_the_host(self, answer: str) -> None:
        running = _serving("sources", lambda arguments: answer)
        try:
            source = _source(running)
            with pytest.raises(ChunkSourceError) as failure:
                source.sources("exposure", 5)
            assert source.host in str(failure.value)
            assert "same ish release" in str(failure.value)
        finally:
            running.close()
