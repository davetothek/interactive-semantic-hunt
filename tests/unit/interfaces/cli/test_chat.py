"""Test the loop of `ish chat`: the questions, the commands, and the failures."""

import logging
from pathlib import Path

import pytest

from ish.application.answer import SEARCHING, Answer, AnswerPart, AnswerStep
from ish.application.ports.language_model import LanguageModelError
from ish.domain.chunk import Chunk
from ish.interfaces.cli.chat import HELP, PROMPT, converse

WATCHDOG = Chunk(Path("docs/boot.md"), "", "section", "markdown", "Watchdog", 1, 3)


class Session:
    """A session that answers each question from one chunk, and records it."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.asked: list[tuple[str, bool, object]] = []
        self.prepared: list[bool] = []
        self.no_model = False

    def prepare(self, brief: bool = False) -> None:
        if self.no_model:
            raise LanguageModelError(
                "No language model is set. Set language_model to answer questions."
            )
        self.prepared.append(brief)

    def ask(self, question, *, on_step=None, brief=False, reading=None) -> Answer:
        self.asked.append((question, brief, reading))
        if on_step:
            on_step(AnswerStep(SEARCHING))
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        return Answer(
            parts=(AnswerPart("The watchdog resets the board.", (WATCHDOG,)),),
            covered=True,
            sources=(WATCHDOG,),
        )

    def link(self, chunk: Chunk) -> str:
        return "https://docs.example.invalid/tree/docs/boot.md#L1-3"

    def kind_of(self, chunk: Chunk) -> str:
        return "doc"


def _lines(*typed: str | BaseException):
    """Return a reader that gives *typed* in turn, then ends the input."""
    left = list(typed)
    prompts: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        if not left:
            raise EOFError
        item = left.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    read.prompts = prompts
    return read


def _chat(session: Session, *typed, brief: bool = True) -> int:
    steps: list[object] = []
    return converse(
        session,
        brief=brief,
        report=lambda ticker, step: steps.append(step),
        read_line=_lines(*typed),
    )


BRIEF = "The watchdog resets the board.[1]\n\n[1] docs/boot.md:1-3\n\n"


class TestQuestions:
    def test_a_question_gets_a_brief_cited_reply(self, capsys) -> None:
        session = Session()
        assert _chat(session, "what does the watchdog do") == 0
        assert capsys.readouterr().out == BRIEF + "\n"
        assert session.asked[0][:2] == ("what does the watchdog do", True)
        assert session.prepared == [True]

    def test_full_mode_replies_with_the_full_answer(self, capsys) -> None:
        session = Session()
        assert _chat(session, "why", brief=False) == 0
        out = capsys.readouterr().out
        assert out.startswith("# why\n")
        assert "## Sources" in out
        assert session.prepared == [False]

    def test_an_empty_line_asks_nothing(self, capsys) -> None:
        session = Session()
        assert _chat(session, "", "   ") == 0
        assert session.asked == []

    def test_the_prompt_names_ish(self) -> None:
        read = _lines()
        converse(Session(), brief=True, report=lambda t, s: None, read_line=read)
        assert read.prompts == [PROMPT]


class TestCommands:
    def test_more_writes_the_full_answer_of_the_last_question(self, capsys) -> None:
        session = Session()
        assert _chat(session, "why", "/more") == 0
        out = capsys.readouterr().out
        assert out.startswith(BRIEF)
        assert "# why\n" in out
        question, brief, reading = session.asked[1]
        assert (question, brief) == ("why", False)
        assert reading.only == (WATCHDOG,)

    def test_more_before_an_answer_says_so(self, capsys) -> None:
        session = Session()
        assert _chat(session, "/more") == 0
        assert "There is no answer yet" in capsys.readouterr().err
        assert session.asked == []

    def test_sources_lists_what_the_last_answer_read(self, capsys) -> None:
        assert _chat(Session(), "why", "/sources") == 0
        assert capsys.readouterr().out.endswith(
            "1. docs/boot.md:1-3  section  Watchdog\n\n"
        )

    def test_sources_before_an_answer_says_so(self, capsys) -> None:
        assert _chat(Session(), "/sources") == 0
        assert "There is no answer yet" in capsys.readouterr().err

    def test_sources_of_an_answer_that_read_nothing(self, capsys, monkeypatch) -> None:
        session = Session()
        monkeypatch.setattr(
            session,
            "ask",
            lambda *a, **k: Answer(parts=(), covered=False, sources=()),
        )
        assert _chat(session, "why", "/sources") == 0
        assert "The last answer read no chunk." in capsys.readouterr().err

    def test_new_forgets_the_chat(self, capsys) -> None:
        session = Session()
        assert _chat(session, "first", "/new", "second") == 0
        assert "The chat starts again." in capsys.readouterr().err
        assert session.asked[1][0] == "second"

    def test_help_names_the_commands(self, capsys) -> None:
        assert _chat(Session(), "/help") == 0
        # The newline after the help is the one the end of the input writes.
        assert capsys.readouterr().out == HELP + "\n"

    def test_quit_ends_the_chat(self) -> None:
        session = Session()
        assert _chat(session, "/quit", "never asked") == 0
        assert session.asked == []

    def test_an_unknown_command_names_help(self, capsys) -> None:
        session = Session()
        assert _chat(session, "/what now") == 0
        assert "/what is no command of the chat. /help names them." in (
            capsys.readouterr().err
        )
        assert session.asked == []


class TestFailures:
    def test_no_language_model_stops_before_any_question(self, caplog) -> None:
        session = Session()
        session.no_model = True
        assert _chat(session, "why") == 1
        assert "Set language_model" in caplog.text
        assert session.asked == []

    def test_a_failed_reply_says_why_and_the_chat_goes_on(self, capsys, caplog) -> None:
        session = Session(RuntimeError("Cannot reach the server."))
        with caplog.at_level(logging.ERROR):
            assert _chat(session, "first", "second") == 0
        assert "Cannot reach the server." in caplog.text
        assert capsys.readouterr().out == BRIEF + "\n"
        assert len(session.asked) == 2

    def test_ctrl_c_during_a_reply_stops_it_and_the_chat_goes_on(self, capsys) -> None:
        session = Session(KeyboardInterrupt())
        assert _chat(session, "first", "second") == 0
        captured = capsys.readouterr()
        assert "ish: stopped" in captured.err
        assert captured.out == BRIEF + "\n"

    @pytest.mark.parametrize("end", [EOFError(), KeyboardInterrupt()])
    def test_ctrl_d_or_ctrl_c_at_the_prompt_ends_the_chat(self, end, capsys) -> None:
        assert _chat(Session(), end) == 0
        assert capsys.readouterr().out == "\n"
