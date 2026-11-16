"""Test how the settings decide the check of the claims of an answer."""

from pathlib import Path

import pytest

from ish import bootstrap
from ish.application.ports.language_model import (
    STATED,
    Passage,
    Quote,
    Reply,
    Verdicts,
)
from ish.interfaces.python.api import Ish, checks, sizes
from ish.interfaces.python.remote import RemoteIsh
from ish.settings import Settings
from tests.unit.interfaces.python.test_remote import (
    EXPOSURE,
    QUESTION,
    URL,
    Server,
    _found,
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\nHow to configure it.\n")
    return tmp_path


@pytest.fixture()
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the embedder so no model or daemon is needed."""

    class Fake:
        model_name = "fake"

        def embed_documents(self, texts):
            return [[float(len(t)), 1.0] for t in texts]

        def embed_query(self, text):
            return [float(len(text)), 1.0]

    monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())


class Named:
    def __init__(self, name: str) -> None:
        self.model_name = name


class TestSizes:
    def test_the_settings_say_how_much_an_answer_reads(self) -> None:
        chosen = Settings(
            ask_chunks=4,
            ask_chars=9_000,
            ask_search_chunks=2,
            ask_searches=1,
            rerank_preview_chars=200,
            check_context_chars=100,
        )
        assert sizes(chosen) == {
            "pick": 4,
            "budget_chars": 9_000,
            "search_chunks": 2,
            "searches_a_turn": 1,
            "preview_chars": 200,
            "check_context_chars": 100,
        }


class TestChecks:
    MODEL, RERANK = Named("answer"), Named("rerank")

    def test_the_model_that_wrote_the_answer_checks_it_by_default(self) -> None:
        found = checks(Settings(), self.MODEL, self.RERANK, brief=False)
        assert found == {"challenge": True, "judge": self.MODEL, "repairs": 1}

    def test_the_rerank_model_checks_when_the_settings_ask(self) -> None:
        settings = Settings(check_model="rerank")
        assert checks(settings, self.MODEL, self.RERANK, False)["judge"] is self.RERANK

    def test_the_answer_model_checks_when_no_rerank_model_is_set(self) -> None:
        settings = Settings(check_model="rerank")
        assert checks(settings, self.MODEL, None, False)["judge"] is self.MODEL

    def test_a_brief_answer_is_neither_checked_nor_challenged(self) -> None:
        """A brief answer cites nothing, and behaves as it did with no check."""
        assert checks(Settings(), self.MODEL, None, brief=True) == {}

    def test_the_check_can_be_turned_off(self) -> None:
        settings = Settings(check_claims=False, challenge=False)
        assert checks(settings, self.MODEL, None, False) == {"challenge": False}

    def test_a_negative_count_of_repairs_runs_none(self) -> None:
        settings = Settings(repair_turns=-3)
        assert checks(settings, self.MODEL, None, False)["repairs"] == 0


class Model:
    """Answer with a claim the guide states, and find each claim stated."""

    model_name = "checked"

    def __init__(self, quote: str) -> None:
        self.quote = quote
        self.checked: list[str] = []

    def answer(self, question, sources):
        return Reply(
            passages=(
                Passage("It explains the setup.", ("1",), (Quote("1", self.quote),)),
            ),
            covered=True,
        )

    def check(self, claims):
        self.checked += [c.text for c in claims]
        return Verdicts(tuple(STATED for _ in claims))


class TestAnAnswerIsChecked:
    def test_a_local_answer_checks_its_claims(
        self,
        project: Path,
        offline,
        monkeypatch,
    ) -> None:
        made = Model("How to configure it.")
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: made)
        with Ish(project, settings=Settings(no_cache=True, git=False)) as ish:
            answer = ish.ask("how to configure", type=("doc",))
        assert made.checked == ["It explains the setup."]
        assert (answer.check_model, answer.claims_checked) == ("checked", 1)

    def test_an_answer_of_a_server_checks_its_claims(self, monkeypatch) -> None:
        made = Model("Text of exposure.md.")
        server = Server({QUESTION: _found(EXPOSURE)})
        monkeypatch.setattr(bootstrap, "build_chunk_source", lambda settings: server)
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s, **_: made)
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: None)
        answer = RemoteIsh(Settings(remote=URL)).ask(QUESTION)
        assert made.checked == ["It explains the setup."]
        assert answer.covered and answer.unconfirmed == ()


@pytest.mark.parametrize(
    "name", ["check_claims", "check_model", "repair_turns", "challenge"]
)
def test_ish_remote_takes_the_options_of_the_check(name: str) -> None:
    from ish.interfaces.cli.remote import OPTIONS

    assert name in OPTIONS
