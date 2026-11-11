"""Run `ish --ask` end to end, with a fake embedder and a fake model."""

import sys
from pathlib import Path

import pytest

from ish import bootstrap
from ish.application.ports.language_model import (
    LanguageModelError,
    Passage,
    Reply,
)
from ish.interfaces.cli.main import main

FLAGS = ["--no-cache", "--no-git"]


class FakeEmbedder:
    model_name = "fake"

    def embed_documents(self, texts):
        return [[float("watchdog" in t), 1.0] for t in texts]

    def embed_query(self, text):
        return [1.0, 1.0]


class FakeModel:
    model_name = "fake-model"

    def __init__(self, covered: bool = True) -> None:
        self.covered = covered
        self.questions: list[str] = []

    def answer(self, question, sources):
        self.questions.append(question)
        watchdog = next(s.id for s in sources if "watchdog" in s.text)
        return Reply(
            passages=(
                Passage("The watchdog resets the board", (watchdog,)),
                Passage("."),
            ),
            covered=self.covered,
        )


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "boot.md").write_text("# Watchdog\n\nThe watchdog resets the board.\n")
    (docs / "other.md").write_text("# Colors\n\nThe sky is blue.\n")
    monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: FakeEmbedder())
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _model(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(bootstrap, "build_language_model", lambda settings, **_: model)


class TestAsk:
    def test_the_answer_cites_its_source_by_number(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        model = FakeModel()
        _model(monkeypatch, model)
        code = main(["--ask", "what does the watchdog do", str(project), *FLAGS])
        out = capsys.readouterr().out
        assert code == 0
        assert model.questions == ["what does the watchdog do"]
        heading, answer, sources_heading, source = [
            block.strip() for block in out.split("\n\n")
        ]
        assert heading == "# what does the watchdog do"
        assert answer == "The watchdog resets the board [^1]."
        assert sources_heading == "## Sources"
        link, about, where = source.split("\n")
        assert link.startswith("[^1]: [boot.md:1-3](file://")
        assert link.endswith("/docs/boot.md#L1-3)")
        assert about == "    doc · Watchdog"
        assert where == "    docs/"

    def test_stderr_ends_with_the_time_table_and_the_version(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        from ish.interfaces.cli.args import installed_version

        _model(monkeypatch, FakeModel())
        main(["--ask", "what does the watchdog do", str(project), *FLAGS])
        captured = capsys.readouterr()
        lines = captured.err.rstrip().splitlines()
        assert lines[-1] == f"ish {installed_version()}"
        assert lines[-2] == ""
        assert any(
            line.startswith("answer ") and "fake-model" in line for line in lines
        )
        assert any(line.startswith("total ") for line in lines)
        assert "stage" not in captured.out

    def test_a_blank_line_sets_the_usage_apart(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        main(["--ask", "what does the watchdog do", str(project), *FLAGS])
        err = capsys.readouterr().err
        assert "\n\nstage " in "\n" + err

    def test_color_always_greys_the_usage_and_leaves_the_answer_plain(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        from ish.interfaces.cli.terminal import GREY

        _model(monkeypatch, FakeModel())
        main(["--ask", "what does the watchdog do", str(project), *FLAGS])
        plain = capsys.readouterr()
        _model(monkeypatch, FakeModel())
        args = ["--ask", "what does the watchdog do", str(project), *FLAGS]
        main([*args, "--color", "always"])
        styled = capsys.readouterr()
        assert styled.out == plain.out
        assert GREY + "stage " in styled.err and GREY + "ish " in styled.err

    def test_render_shows_a_terminal_the_answer_without_its_markup(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        args = ["--ask", "what does the watchdog do", str(project), *FLAGS]
        assert main([*args, "--render", "--color", "never"]) == 0
        out = capsys.readouterr().out
        assert "The watchdog resets the board [1]." in out
        assert " 1 boot.md:1-3" in out and "(file://" in out
        assert "#" not in out.replace("#L1-3", "") and "[^1]" not in out

    def test_render_keeps_to_a_width_that_reads_well(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        from ish.interfaces.cli.terminal import MAX_WIDTH

        _model(monkeypatch, FakeModel())
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        monkeypatch.setenv("COLUMNS", "300")
        args = ["--ask", "what does the watchdog do", str(project), *FLAGS]
        main([*args, "--render", "--color", "never"])
        lines = capsys.readouterr().out.split("\n")
        # Rich centers the title, so the first line shows the width in use.
        assert len(lines[0]) > 60
        assert all(len(line) <= MAX_WIDTH for line in lines)

    def test_render_leaves_a_pipe_its_markdown(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        args = ["--ask", "what does the watchdog do", str(project), *FLAGS]
        main(args)
        plain = capsys.readouterr().out
        _model(monkeypatch, FakeModel())
        main([*args, "--render"])
        assert capsys.readouterr().out == plain

    def test_a_pipe_gets_plain_markdown(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel())
        main(["--ask", "what does the watchdog do", str(project), *FLAGS])
        captured = capsys.readouterr()
        assert "\033[" not in captured.out and "\033[" not in captured.err

    def test_not_covered_lists_the_closest_sources_and_exits_2(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel(covered=False))
        code = main(["--ask", "what is the MTBF", str(project), *FLAGS])
        out = capsys.readouterr().out
        assert code == 2
        assert out.startswith("# what is the MTBF\n\n> The documents do not answer")
        assert "## Closest sources" in out
        assert "[boot.md:1-3](file://" in out
        assert "[other.md:1-3](file://" in out

    def test_a_question_is_required(self, project: Path, caplog) -> None:
        assert main(["--ask", str(project), *FLAGS]) == 1
        assert "--ask needs a question" in caplog.text

    def test_no_language_model_set_is_an_error(
        self, project: Path, capsys, caplog
    ) -> None:
        code = main(["--ask", "anything", str(project), *FLAGS])
        assert code == 1
        assert capsys.readouterr().out == ""
        assert "No language model is set" in caplog.text

    def test_a_model_error_is_reported(
        self, project: Path, monkeypatch, caplog
    ) -> None:
        def broken(settings, **_):
            raise LanguageModelError("The Anthropic API did not answer within 5 s.")

        monkeypatch.setattr(bootstrap, "build_language_model", broken)
        assert main(["--ask", "anything", str(project), *FLAGS]) == 1
        assert "within 5 s" in caplog.text

    @pytest.mark.parametrize("line", [[*FLAGS, "--", "ask"], ["search", *FLAGS, "ask"]])
    def test_the_word_ask_can_still_be_a_query(
        self, line: list[str], project: Path, monkeypatch, capsys
    ) -> None:
        """The first word "ask" is a command. Two forms search for it."""

        def refuse(settings, **_):
            raise AssertionError("a search built a language model")

        monkeypatch.setattr(bootstrap, "build_language_model", refuse)
        assert main([*line, str(project)]) == 0
        assert "docs/" in capsys.readouterr().out


class TestProgressStaysOffStdout:
    def test_the_steps_go_to_the_ticker_and_stdout_holds_the_answer(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        from ish.interfaces.cli import main as cli

        shown: list[str] = []
        stopped: list[bool] = []

        class Recorder:
            def show(self, text: str) -> None:
                shown.append(text)

            def stop(self) -> None:
                stopped.append(True)

        monkeypatch.setattr(cli, "Ticker", Recorder)
        _model(monkeypatch, FakeModel())
        assert main(["--ask", "what does the watchdog do", str(project), *FLAGS]) == 0
        assert shown[0] == "Searching"
        assert shown[-1] == "fake-model writing the answer"
        assert stopped == [True]
        assert capsys.readouterr().out.startswith("# what does the watchdog do")

    def test_the_ticker_stops_when_the_model_fails(
        self, project: Path, monkeypatch
    ) -> None:
        from ish.interfaces.cli import main as cli

        stopped: list[bool] = []

        class Recorder:
            def show(self, text: str) -> None:
                pass

            def stop(self) -> None:
                stopped.append(True)

        class Broken:
            model_name = "broken"

            def answer(self, question, sources):
                raise LanguageModelError("down")

        monkeypatch.setattr(cli, "Ticker", Recorder)
        _model(monkeypatch, Broken())
        assert main(["--ask", "anything", str(project), *FLAGS]) == 1
        assert stopped == [True]


class TestInterrupt:
    """Ctrl-C while a model works leaves a clean line and exit code 130."""

    def test_ctrl_c_exits_130_with_no_traceback(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        class Interrupted:
            model_name = "slow"

            def answer(self, question, sources):
                raise KeyboardInterrupt

        _model(monkeypatch, Interrupted())
        code = main(["--ask", "anything", str(project), *FLAGS])
        out, err = capsys.readouterr()
        assert code == 130
        assert out == ""
        assert err.endswith("ish: interrupted\n")
        assert "Traceback" not in err

    def test_ctrl_c_stops_the_ticker(self, project: Path, monkeypatch) -> None:
        from ish.interfaces.cli import main as cli

        stops: list[bool] = []

        class Recorder:
            def show(self, text: str) -> None:
                pass

            def stop(self) -> None:
                stops.append(True)

        class Interrupted:
            model_name = "slow"

            def answer(self, question, sources):
                raise KeyboardInterrupt

        monkeypatch.setattr(cli, "Ticker", Recorder)
        _model(monkeypatch, Interrupted())
        assert main(["--ask", "anything", str(project), *FLAGS]) == 130
        assert stops


class TestFollowUpSearchesStand:
    def test_each_follow_up_search_is_left_standing(
        self, project: Path, monkeypatch
    ) -> None:
        from ish.application.ports.language_model import Passage, Reply
        from ish.interfaces.cli import main as cli

        notes: list[str] = []

        class Recorder:
            def show(self, text: str) -> None:
                pass

            def note(self, text: str) -> None:
                notes.append(text)

            def stop(self) -> None:
                pass

        class Agent:
            model_name = "agent"

            def step(self, question, sources, searched, turns_left):
                if not searched and turns_left:
                    return Reply(
                        passages=(), covered=False, searches=("the reset command",)
                    )
                return Reply(passages=(Passage("Found.", ("1",)),), covered=True)

        monkeypatch.setattr(cli, "Ticker", Recorder)
        _model(monkeypatch, Agent())
        assert main(["--ask", "q", str(project), *FLAGS, "--ask-turns", "2"]) == 0
        assert notes == ["Searching again: the reset command"]


class TestBrief:
    def test_brief_prints_the_short_answer_alone(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        from ish.application.ports.language_model import Block, Passage, Reply

        kinds: list[bool] = []

        class Model:
            model_name = "m"

            def answer(self, question, sources):
                return Reply(
                    passages=(Passage("It resets the board.", ("1",)),),
                    covered=True,
                    blocks=(
                        Block(
                            "summary",
                            passages=(Passage("It resets the board.", ("1",)),),
                        ),
                    ),
                )

        def build(settings, *, brief=False):
            kinds.append(brief)
            return Model()

        monkeypatch.setattr(bootstrap, "build_language_model", build)
        code = main(["--brief", "what does the watchdog do", str(project), *FLAGS])
        assert code == 0
        assert capsys.readouterr().out == "It resets the board.\n"
        assert kinds == [True]

    def test_render_leaves_the_short_answer_as_plain_text(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        args = ["--brief", "what does the watchdog do", str(project), *FLAGS]
        _model(monkeypatch, FakeModel())
        main(args)
        plain = capsys.readouterr().out
        _model(monkeypatch, FakeModel())
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        main([*args, "--render"])
        assert plain.strip() and capsys.readouterr().out == plain

    def test_brief_with_nothing_covered_exits_2(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        _model(monkeypatch, FakeModel(covered=False))
        assert main(["--brief", "what is the MTBF", str(project), *FLAGS]) == 2
        assert capsys.readouterr().out == "The documents do not answer this.\n"
