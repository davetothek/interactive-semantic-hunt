"""Test the session that answers from the chunks of a server."""

from pathlib import Path

import pytest

from ish import bootstrap
from ish.application.answer import AnswerStep, AnswerTerm
from ish.application.glossary import Definition
from ish.application.ports.language_model import (
    LanguageModelError,
    Passage,
    Reply,
    Selection,
)
from ish.application.sources import ChunkText, Sources
from ish.domain.chunk import Chunk
from ish.interfaces.python.api import ASK_LIMIT
from ish.interfaces.python.remote import RemoteIsh
from ish.settings import Settings

URL = "https://docs.example.invalid/mcp"
QUESTION = "what runs before the gain"


def _chunk(name: str, start: int = 1) -> Chunk:
    return Chunk(Path(name), "", "section", "markdown", name, start, start + 2)


EXPOSURE = _chunk("exposure.md")
GAIN = _chunk("gain.md")
TERMS = _chunk("terms.md")


class Server:
    """A chunk source that records each call and returns what the test set."""

    host = "docs.example.invalid"
    version = "1.2.3"

    def __init__(self, found: dict[str, Sources] | None = None) -> None:
        self.found = found or {}
        self.lines = "exposure.md:1-3  section  Exposure"
        self.searches: list[tuple] = []
        self.calls: list[tuple] = []

    def search(self, query, limit, shape, *, lang=(), under="", type=()):
        self.searches.append((query, limit, shape, tuple(lang), under, tuple(type)))
        return self.lines

    def sources(self, query, limit, *, lang=(), under="", type=()):
        self.calls.append((query, limit, tuple(lang), under, tuple(type)))
        return self.found.get(query, Sources((), ()))

    def link(self, chunk):
        return f"https://docs.example.invalid/tree/{chunk.path}"

    def kind_of(self, chunk):
        return "doc"


class Model:
    """A language model that records what it read and cites the first source."""

    model_name = "writer"

    def __init__(self, text: str = "The exposure block runs first.") -> None:
        self.text = text
        self.read: list[list[tuple[str, str]]] = []
        self.steps: list[int] = []
        self.follow: list[tuple[str, ...]] = []

    def _reply(self, sources, searches=()):
        self.read.append([(source.title, source.text) for source in sources])
        return Reply(
            passages=(Passage(self.text, ("1",)),), covered=True, searches=searches
        )

    def answer(self, question, sources):
        return self._reply(sources)

    def step(self, question, sources, searched, turns_left):
        self.steps.append(turns_left)
        return self._reply(sources, self.follow.pop(0) if self.follow else ())


class Silent:
    """A language model that no test may ask."""

    model_name = "silent"

    def answer(self, question, sources):
        raise AssertionError("the model was asked")

    step = select = answer


def _found(*chunks: Chunk, terms: tuple[Definition, ...] = ()) -> Sources:
    texts = tuple(
        ChunkText(chunk, 0.9 - n / 10, f"Text of {chunk.symbol}.")
        for n, chunk in enumerate(chunks)
    )
    return Sources(texts, terms)


@pytest.fixture()
def server(monkeypatch) -> Server:
    made = Server({QUESTION: _found(EXPOSURE, GAIN)})
    monkeypatch.setattr(bootstrap, "build_chunk_source", lambda settings: made)
    return made


@pytest.fixture()
def model(monkeypatch) -> Model:
    made = Model()
    monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: made)
    monkeypatch.setattr(bootstrap, "build_reranker", lambda s: None)
    return made


def _settings(**changes) -> Settings:
    return Settings(remote=URL, **changes)


class TestServer:
    def test_the_session_names_the_host_and_the_version(self, server: Server) -> None:
        remote = RemoteIsh(_settings())
        assert (remote.host, remote.version) == ("docs.example.invalid", "1.2.3")

    def test_the_source_is_built_from_the_settings(self, monkeypatch) -> None:
        seen: list[Settings] = []

        def build(settings: Settings) -> Server:
            seen.append(settings)
            return Server()

        monkeypatch.setattr(bootstrap, "build_chunk_source", build)
        settings = _settings()
        RemoteIsh(settings)
        assert seen == [settings]

    def test_a_link_and_a_type_come_from_the_server(self, server: Server) -> None:
        remote = RemoteIsh(_settings())
        assert remote.link(GAIN) == "https://docs.example.invalid/tree/gain.md"
        assert remote.kind_of(GAIN) == "doc"


class TestSearch:
    def test_the_lines_of_the_server_come_back(self, server: Server) -> None:
        assert RemoteIsh(_settings()).search("exposure") == server.lines

    def test_the_limit_and_the_shape_are_those_of_the_settings(
        self, server: Server
    ) -> None:
        RemoteIsh(_settings(limit=7, format="grep")).search("exposure")
        assert server.searches == [("exposure", 7, "grep", (), "", ())]

    def test_a_limit_of_the_call_wins(self, server: Server) -> None:
        RemoteIsh(_settings(limit=7)).search("exposure", 3)
        assert server.searches[0][1] == 3

    def test_a_filter_of_the_call_travels(self, server: Server) -> None:
        remote = RemoteIsh(_settings())
        remote.search("exposure", lang=["markdown"], under="docs", type=["doc"])
        assert server.searches[0][3:] == (("markdown",), "docs", ("doc",))

    def test_a_filter_of_the_settings_stays_on_this_machine(
        self, server: Server
    ) -> None:
        # A filter in a config file is for a tree of this machine.
        settings = _settings(lang=("python",), under="src", type=("code",))
        RemoteIsh(settings).search("exposure")
        assert server.searches[0][3:] == ((), "", ())


class TestAsk:
    def test_the_model_reads_the_text_the_server_sent(
        self, server: Server, model: Model
    ) -> None:
        answer = RemoteIsh(_settings()).ask(QUESTION)
        assert answer.covered
        assert answer.sources == (EXPOSURE, GAIN)
        assert [text for _, text in model.read[0]] == [
            "Text of exposure.md.",
            "Text of gain.md.",
        ]

    def test_the_model_reads_no_file_of_this_machine(
        self, server: Server, model: Model, tmp_path, monkeypatch
    ) -> None:
        # A file of this machine at the path the server names is not the source.
        (tmp_path / "exposure.md").write_text("one\ntwo\nthree\n")
        monkeypatch.chdir(tmp_path)
        RemoteIsh(_settings()).ask(QUESTION)
        assert model.read[0][0][1] == "Text of exposure.md."

    def test_the_first_search_asks_for_the_answer_limit(
        self, server: Server, model: Model
    ) -> None:
        RemoteIsh(_settings()).ask(QUESTION)
        assert server.calls == [(QUESTION, ASK_LIMIT, (), "", ())]

    def test_a_filter_of_the_call_travels(self, server: Server, model: Model) -> None:
        remote = RemoteIsh(_settings(lang=("python",)))
        remote.ask(QUESTION, lang=["markdown"], under="docs", type=["doc"])
        assert server.calls[0][2:] == (("markdown",), "docs", ("doc",))

    def test_a_search_that_finds_nothing_asks_no_model(
        self, server: Server, monkeypatch
    ) -> None:
        silent = Silent()
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: silent)
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: silent)
        answer = RemoteIsh(_settings(ask_turns=3)).ask("nothing matches this")
        assert not answer.covered
        assert answer.sources == ()

    def test_each_step_reaches_the_caller(self, server: Server, model: Model) -> None:
        steps: list[AnswerStep] = []
        RemoteIsh(_settings()).ask(QUESTION, on_step=steps.append)
        assert [step.stage for step in steps] == ["searching", "reading", "writing"]

    def test_brief_builds_the_brief_model(self, server: Server, monkeypatch) -> None:
        built: list[bool] = []

        def build(settings: Settings, *, brief: bool = False) -> Model:
            built.append(brief)
            return Model()

        monkeypatch.setattr(bootstrap, "build_language_model", build)
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: None)
        remote = RemoteIsh(_settings())
        remote.ask(QUESTION, brief=True)
        remote.ask(QUESTION, brief=True)
        remote.ask(QUESTION)
        # One model for each kind of answer, however many questions.
        assert built == [True, False]


class TestModelPolicy:
    """The settings of this machine decide which model reads the text."""

    def test_no_model_set_refuses_before_any_request(self, server: Server) -> None:
        with pytest.raises(LanguageModelError, match="language_model"):
            RemoteIsh(_settings()).ask(QUESTION)
        assert server.calls == []

    def test_a_model_that_is_not_allowed_refuses_before_any_request(
        self, server: Server
    ) -> None:
        settings = _settings(language_model="claude-cli")
        with pytest.raises(LanguageModelError, match="allowed_language_models"):
            RemoteIsh(settings).ask(QUESTION)
        assert server.calls == []

    def test_a_rerank_model_that_is_not_allowed_refuses_before_any_request(
        self, server: Server, monkeypatch
    ) -> None:
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: Model())
        settings = _settings(language_model="claude-cli", rerank_model="small")
        with pytest.raises(LanguageModelError, match="allowed_language_models"):
            RemoteIsh(settings).ask(QUESTION)
        assert server.calls == []


class TestRerank:
    class Chooser:
        model_name = "chooser"

        def __init__(self) -> None:
            self.seen: list[tuple[int, int]] = []

        def select(self, question, candidates, limit):
            self.seen.append((len(candidates), limit))
            return Selection(ids=("2",))

    @pytest.fixture()
    def chooser(self, monkeypatch, model: Model) -> "TestRerank.Chooser":
        made = self.Chooser()
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: made)
        return made

    def test_the_rerank_runs_on_this_machine(
        self, server: Server, model: Model, chooser
    ) -> None:
        answer = RemoteIsh(_settings(rerank_candidates=40)).ask(QUESTION)
        assert chooser.seen == [(2, ASK_LIMIT)]
        assert answer.sources == (GAIN,)
        assert answer.rerank_model == "chooser"

    def test_the_search_asks_for_the_rerank_candidates(
        self, server: Server, model: Model, chooser
    ) -> None:
        RemoteIsh(_settings(rerank_candidates=40)).ask(QUESTION)
        assert server.calls[0][1] == 40

    def test_the_reranker_is_built_once(
        self, server: Server, model: Model, monkeypatch
    ) -> None:
        built: list[int] = []
        # A session with no rerank model does not look for one again.
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: built.append(1))
        remote = RemoteIsh(_settings())
        remote.ask(QUESTION)
        remote.ask(QUESTION)
        assert built == [1]


class TestFollowUp:
    def test_a_follow_up_search_goes_to_the_server(
        self, server: Server, model: Model
    ) -> None:
        server.found["analog gain"] = _found(TERMS)
        model.follow = [("analog gain",)]
        answer = RemoteIsh(_settings(ask_turns=2)).ask(QUESTION, under="docs")
        assert [call[0] for call in server.calls] == [QUESTION, "analog gain"]
        # The filter of the call narrows the follow-up search as well.
        assert [call[3] for call in server.calls] == ["docs", "docs"]
        assert answer.searches == ("analog gain",)
        assert answer.sources == (EXPOSURE, GAIN, TERMS)
        assert model.read[1][2] == ("terms.md:1-3 terms.md", "Text of terms.md.")

    def test_the_turns_of_the_settings_reach_the_model(
        self, server: Server, model: Model
    ) -> None:
        RemoteIsh(_settings(ask_turns=2)).ask(QUESTION)
        assert model.steps == [2]

    def test_the_turns_of_a_question_win(self, server: Server, model: Model) -> None:
        remote = RemoteIsh(_settings(ask_turns=2))
        remote.ask(QUESTION, turns=4)
        remote.ask(QUESTION, turns=0)
        assert model.steps == [4]
        assert len(model.read) == 2


class TestTerms:
    def test_a_term_of_the_server_names_an_acronym_of_the_answer(
        self, server: Server, monkeypatch
    ) -> None:
        # No text the client read spells out AE. The server found it in its tree.
        term = Definition("AE", "Auto Exposure", TERMS)
        server.found[QUESTION] = _found(EXPOSURE, terms=(term,))
        model = Model("The AE block runs first.")
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: model)
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: None)
        answer = RemoteIsh(_settings()).ask(QUESTION)
        assert answer.terms == (AnswerTerm("AE", "Auto Exposure", (TERMS,)),)

    def test_an_acronym_no_text_spells_out_gets_no_meaning(
        self, server: Server, monkeypatch
    ) -> None:
        model = Model("The AGC block runs first.")
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: model)
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: None)
        assert RemoteIsh(_settings()).ask(QUESTION).terms == ()
