"""Test the stdio tools that read the tree of a server."""

import json

import pytest

from ish import bootstrap
from ish.interfaces.mcp.remote import NO_PATH, RemoteTools
from ish.interfaces.mcp.server import IshTools
from ish.settings import Settings
from tests.unit.interfaces.python.test_remote import (
    EXPOSURE,
    QUESTION,
    Model,
    Server,
    Silent,
    _found,
    _settings,
)


@pytest.fixture()
def server(monkeypatch) -> Server:
    made = Server({QUESTION: _found(EXPOSURE)})
    monkeypatch.setattr(bootstrap, "build_chunk_source", lambda settings: made)
    return made


@pytest.fixture()
def model(monkeypatch) -> Model:
    made = Model()
    monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: made)
    monkeypatch.setattr(bootstrap, "build_reranker", lambda s: None)
    return made


def _tools(**changes) -> RemoteTools:
    return RemoteTools(_settings(**changes))


class TestTools:
    def test_each_tool_that_reads_the_server_travels(self, server: Server) -> None:
        # A refresh changes the server, so it stays with the operator.
        assert [tool.name for tool in _tools().tools()] == [
            "search_code",
            "ask",
            "list_chunks",
            "index_status",
            "complete_filter",
        ]

    def test_each_tool_takes_what_the_local_tool_takes(
        self, server: Server, tmp_path
    ) -> None:
        local = {
            tool.name: tool
            for tool in IshTools(Settings(no_cache=True, git=False), tmp_path).tools()
        }
        for tool in _tools().tools():
            assert tool.schema == local[tool.name].schema
            assert tool.description == local[tool.name].description


class TestSearch:
    def test_it_returns_the_lines_of_the_server(self, server: Server) -> None:
        assert _tools().search({"query": "exposure"}) == server.lines

    def test_the_options_of_a_call_travel(self, server: Server) -> None:
        call = {"query": "exposure", "limit": 3, "format": "grep"}
        call |= {"lang": ["markdown"], "under": "docs", "type": ["doc"]}
        _tools().search(call)
        assert server.searches == [
            ("exposure", 3, "grep", ("markdown",), "docs", ("doc",))
        ]

    def test_the_settings_give_the_limit_and_the_shape(self, server: Server) -> None:
        _tools(limit=7, format="grep").search({"query": "exposure"})
        assert server.searches == [("exposure", 7, "grep", (), "", ())]

    def test_a_filter_of_this_machine_stays_here(self, server: Server) -> None:
        _tools(under="src", lang=("python",)).search({"query": "exposure"})
        ((*_, lang, under, kinds),) = server.searches
        assert (lang, under, kinds) == ((), "", ())

    def test_nothing_found_names_the_server(self, server: Server) -> None:
        server.lines = ""
        assert _tools().search({"query": "nothing"}) == (
            "No results for 'nothing' on docs.example.invalid."
        )

    @pytest.mark.parametrize("name", ["search", "ask"])
    def test_a_path_is_refused(self, name: str, server: Server) -> None:
        text = {"search": "query", "ask": "question"}[name]
        with pytest.raises(ValueError, match=NO_PATH):
            getattr(_tools(), name)({text: "exposure", "path": "/srv/docs"})
        assert server.searches == server.calls == []

    @pytest.mark.parametrize(
        ("name", "text"), [("search", "query"), ("ask", "question")]
    )
    def test_an_empty_text_is_refused(self, name: str, text: str, server) -> None:
        with pytest.raises(ValueError, match=f"The '{text}' argument is required."):
            getattr(_tools(), name)({text: "  "})


class TestAsk:
    def test_the_model_of_this_machine_answers(
        self, server: Server, model: Model
    ) -> None:
        reply = json.loads(_tools().ask({"question": QUESTION}))
        assert reply["covered"] is True
        assert reply["answer"] == "The exposure block runs first.[1]"
        (citation,) = reply["citations"]
        assert citation["path"] == "exposure.md"
        assert citation["url"] == "https://docs.example.invalid/tree/exposure.md"
        assert citation["type"] == "doc"
        assert reply["model"] == "writer"
        assert reply["markdown"].startswith(f"# {QUESTION}\n")
        # The model reads the text the server sent, and no file of this machine.
        assert [text for _title, text in model.read[0]] == ["Text of exposure.md."]

    def test_the_reply_names_the_server(self, server: Server, model: Model) -> None:
        reply = json.loads(_tools().ask({"question": QUESTION}))
        assert reply["server"] == {"host": "docs.example.invalid", "version": "1.2.3"}

    def test_brief_gives_the_short_answer(self, server: Server, model: Model) -> None:
        reply = json.loads(_tools().ask({"question": QUESTION, "brief": True}))
        assert reply["answer"] == "The exposure block runs first."
        assert "citations" not in reply

    def test_the_filters_and_the_turns_of_a_call_travel(
        self, server: Server, model: Model
    ) -> None:
        call = {"question": QUESTION, "ask_turns": 2, "under": "docs"}
        _tools().ask(call | {"lang": ["markdown"], "type": ["doc"]})
        assert server.calls[0] == (QUESTION, 10, ("markdown",), "docs", ("doc",))
        assert model.steps == [2]

    def test_too_many_turns_are_refused(self, server: Server, model: Model) -> None:
        with pytest.raises(ValueError, match="'ask_turns' must be from 0 to 5."):
            _tools().ask({"question": QUESTION, "ask_turns": 6})
        assert server.calls == []

    def test_no_language_model_reads_no_text_of_the_server(
        self, server: Server
    ) -> None:
        from ish.application.ports.language_model import LanguageModelError

        with pytest.raises(LanguageModelError, match="language_model"):
            _tools().ask({"question": QUESTION})
        assert server.calls == []

    def test_a_question_the_documents_do_not_cover(
        self, server: Server, monkeypatch
    ) -> None:
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: Silent())
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: None)
        reply = json.loads(_tools().ask({"question": "nothing matches this"}))
        assert reply["covered"] is False
        assert reply["sources"] == []


class TestTheToolsThatRead:
    def test_list_chunks_sends_only_the_options_of_the_call(
        self, server: Server
    ) -> None:
        tools = _tools(under="src", lang=("python",))
        assert tools.list_chunks({"lang": ["markdown"], "format": "grep"}) == "read"
        assert tools.list_chunks({}) == "read"
        assert server.reads == [
            ("list_chunks", {"lang": ["markdown"], "format": "grep"}),
            ("list_chunks", {}),
        ]

    def test_index_status_sends_nothing(self, server: Server) -> None:
        assert _tools().index_status({}) == "read"
        assert server.reads == [("index_status", {})]

    def test_complete_filter_sends_the_query(self, server: Server) -> None:
        assert _tools().complete_filter({"query": "ty"}) == "read"
        assert _tools().complete_filter({}) == "read"
        assert server.reads == [
            ("complete_filter", {"query": "ty"}),
            ("complete_filter", {"query": ""}),
        ]

    @pytest.mark.parametrize("name", ["list_chunks", "index_status", "complete_filter"])
    def test_a_path_is_refused(self, name: str, server: Server) -> None:
        with pytest.raises(ValueError, match=NO_PATH):
            getattr(_tools(), name)({"path": "/srv/docs", "query": "x"})
        assert server.reads == []
