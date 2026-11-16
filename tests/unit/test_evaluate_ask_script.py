"""Test the scoring of the ask evaluation script.

The script lives outside the package, because it measures ish rather
than ships with it. Load it by path.
"""

import hashlib
import importlib.util
import json
import re
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from ish.application.answer import Answer, AnswerPart
from ish.domain.chunk import Chunk
from ish.settings import Settings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_ask.py"


def _load():
    spec = importlib.util.spec_from_file_location("evaluate_ask", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ev = _load()
TREE = Path("/srv/docs")


def _chunk(path: str, start: int, end: int) -> Chunk:
    return Chunk(TREE / path, "t", "section", "asciidoc", "s", start, end)


def _answer(*chunks: Chunk, covered: bool = True) -> Answer:
    return Answer(
        parts=(AnswerPart("x", chunks),) if covered else (),
        covered=covered,
        sources=chunks,
    )


class TestLoad:
    def test_a_set_reads(self, tmp_path) -> None:
        f = tmp_path / "q.yaml"
        f.write_text(
            "- id: q1\n  question: a?\n  expected:\n    - path: a.adoc\n"
            "      lines: '3-9'\n- id: q2\n  question: b?\n  expected: not_covered\n"
        )
        items = ev.load(f)
        assert ev.expected_of(items[0]) == [ev.Expected("a.adoc", 3, 9)]
        assert ev.expected_of(items[1]) is None

    def test_a_single_line_range(self) -> None:
        item = {"expected": [{"path": "a.adoc", "lines": 7}]}
        assert ev.expected_of(item) == [ev.Expected("a.adoc", 7, 7)]

    @pytest.mark.parametrize(
        ("body", "message"),
        [("a: 1\n", "no list"), ("- id: q1\n  question: a?\n", "'expected'")],
    )
    def test_a_bad_set_is_refused(self, tmp_path, body, message) -> None:
        f = tmp_path / "q.yaml"
        f.write_text(body)
        with pytest.raises(ValueError, match=message):
            ev.load(f)


class TestHits:
    want = [ev.Expected("spec/a.adoc", 100, 120)]

    @pytest.mark.parametrize(
        ("chunk", "hit"),
        [
            (_chunk("spec/a.adoc", 110, 130), True),
            (_chunk("spec/a.adoc", 90, 100), True),
            (_chunk("spec/a.adoc", 121, 130), False),
            (_chunk("spec/b.adoc", 100, 120), False),
            (
                Chunk(Path("/elsewhere/spec/a.adoc"), "t", "s", "a", None, 100, 120),
                False,
            ),
        ],
    )
    def test_overlap_in_the_expected_file(self, chunk, hit) -> None:
        assert ev.hits(_answer(chunk), self.want, TREE) is hit


def _served(path: str, start: int, end: int) -> Chunk:
    return Chunk(Path(path), "", "section", "asciidoc", "s", start, end)


class TestHitsFromAServer:
    """A server names a file from the root of its own tree."""

    want = [ev.Expected("spec/a.adoc", 100, 120)]

    @pytest.mark.parametrize(
        ("chunk", "hit"),
        [
            (_served("spec/a.adoc", 110, 130), True),
            (_served("docs/spec/a.adoc", 110, 130), True),
            (_served("docs/spec/a.adoc", 121, 130), False),
            (_served("docs/myspec/a.adoc", 110, 130), False),
            (_served("old-spec/a.adoc", 110, 130), False),
            (_served("spec/a.adoc.bak", 110, 130), False),
        ],
    )
    def test_the_path_ends_with_the_expected_path(self, chunk, hit) -> None:
        assert ev.hits(_answer(chunk), self.want, None) is hit

    def test_a_score_from_a_server(self) -> None:
        item = {
            "id": "q1",
            "question": "a?",
            "expected": [{"path": "spec/a.adoc", "lines": "1-9"}],
        }
        row = ev.score(item, FakeIsh(_answer(_served("docs/spec/a.adoc", 2, 4))), None)
        assert (row.covered, row.hit, row.cited) == (True, True, ["a.adoc:2-4"])


class FakeIsh:
    def __init__(self, answer: Answer | Exception) -> None:
        self.answer = answer

    def ask(self, question: str) -> Answer:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class TestScore:
    covered = {
        "id": "q1",
        "question": "a?",
        "expected": [{"path": "spec/a.adoc", "lines": "1-9"}],
    }
    uncovered = {"id": "q2", "question": "b?", "expected": "not_covered"}

    def test_a_hit(self) -> None:
        row = ev.score(
            self.covered, FakeIsh(_answer(_chunk("spec/a.adoc", 2, 4))), TREE
        )
        assert (row.covered, row.hit, row.cited) == (True, True, ["a.adoc:2-4"])

    def test_not_covered_is_no_hit(self) -> None:
        row = ev.score(self.covered, FakeIsh(_answer(covered=False)), TREE)
        assert (row.covered, row.hit) == (False, False)

    def test_an_error_is_recorded(self) -> None:
        row = ev.score(self.uncovered, FakeIsh(RuntimeError("down")), TREE)
        assert row.error == "down"

    def test_the_summary(self) -> None:
        rows = [
            ev.Scored(
                "q1",
                True,
                covered=True,
                hit=True,
                input_tokens=1000,
                output_tokens=100,
                seconds=2,
            ),
            ev.Scored(
                "q2",
                True,
                covered=True,
                hit=False,
                input_tokens=3000,
                output_tokens=300,
                seconds=4,
            ),
            ev.Scored("q3", False, covered=False, seconds=1),
            ev.Scored("q4", False, error="down", seconds=9),
        ]
        numbers = ev.summary(rows, price_in=2.0, price_out=10.0)
        assert numbers["citation hit rate"] == "1/2 (50 %)"
        assert numbers["answered when covered"] == "2/2 (100 %)"
        assert numbers["refused when not covered"] == "1/2 (50 %)"
        assert numbers["errors"] == 1
        assert numbers["median seconds"] == 3.0
        assert numbers["cost per answer (USD)"] == round((0.003 + 0.009 + 0) / 3, 4)
        assert "| q4 | not covered |" in ev.table(rows, numbers)

    def test_the_summary_names_the_cost_the_backend_reported(self) -> None:
        rows = [
            ev.Scored("q1", True, covered=True, cost_usd=0.01),
            ev.Scored("q2", True, covered=True, cost_usd=0.03),
            ev.Scored("q3", True, covered=True, cost_usd=0.05),
            ev.Scored("q4", True, error="down"),
        ]
        numbers = ev.summary(rows, 0, 0)
        assert numbers["reported cost per answer (USD), mean"] == 0.03
        assert numbers["reported cost per answer (USD), median"] == 0.03

    def test_a_score_keeps_the_cost_of_the_answer(self) -> None:
        answer = Answer(parts=(), covered=False, sources=(), cost_usd=0.02)
        row = ev.score(self.uncovered, FakeIsh(answer), TREE)
        assert row.cost_usd == 0.02

    def test_an_empty_summary(self) -> None:
        numbers = ev.summary([], 0, 0)
        assert numbers["citation hit rate"] == "-"
        assert numbers["median seconds"] == 0


QUESTIONS = """\
- id: q1
  question: a?
  expected:
    - path: spec/a.adoc
      lines: 1-9
"""
MODELS = ["--language-model", "claude-cli", "--answer-model", "claude-haiku-5-5"]
SERVER = "https://ish.example.invalid/mcp"


class Opened(FakeIsh):
    """A session that keeps how the script opened it."""

    def __init__(self, answer: Answer, **given: object) -> None:
        super().__init__(answer)
        self.given = given

    def __enter__(self) -> "Opened":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.given["closed"] = True


class TestMain:
    @pytest.fixture()
    def questions(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setattr(ev, "load_settings", lambda start=None: Settings())
        path = tmp_path / "questions.yaml"
        path.write_text(QUESTIONS)
        return path

    @pytest.mark.parametrize("where", [[], ["tree", "--remote", SERVER]])
    def test_one_of_a_tree_and_a_server(self, questions, where, capsys) -> None:
        with pytest.raises(SystemExit) as stopped:
            ev.main([str(questions), *where, *MODELS])
        assert stopped.value.code == 2
        assert "Give TREE, or --remote URL, and not both." in capsys.readouterr().err

    def test_a_server_gets_the_models_of_this_run(
        self, questions, monkeypatch, capsys
    ) -> None:
        opened = []

        def remote(settings: Settings) -> Opened:
            served = _served("docs/spec/a.adoc", 2, 4)
            opened.append(Opened(_answer(served), settings=settings))
            return opened[0]

        monkeypatch.setattr(ev, "RemoteIsh", remote)
        argv = [str(questions), "--remote", SERVER, *MODELS, "--ask-turns", "3"]
        assert ev.main(argv) == 0
        settings = opened[0].given["settings"]
        assert (settings.remote, settings.ask_turns) == (SERVER, 3)
        assert settings.allowed_language_models == ("claude-cli",)
        assert (settings.language_model, settings.answer_model) == (
            "claude-cli",
            "claude-haiku-5-5",
        )
        assert "| citation hit rate | 1/1 (100 %) |" in capsys.readouterr().out

    def test_a_tree_is_scored_from_its_own_root(
        self, questions, tmp_path, monkeypatch, capsys
    ) -> None:
        tree = tmp_path / "tree"
        opened = []

        def local(path: Path, *, settings: Settings) -> Opened:
            cited = Chunk(path / "spec/a.adoc", "", "section", "asciidoc", "s", 2, 4)
            opened.append(Opened(_answer(cited), path=path, settings=settings))
            return opened[0]

        monkeypatch.setattr(ev, "Ish", local)
        out = tmp_path / "rows.json"
        assert ev.main([str(questions), str(tree), *MODELS, "--out", str(out)]) == 0
        given = opened[0].given
        assert (given["path"], given["closed"]) == (tree.resolve(), True)
        assert given["settings"].remote == ""
        assert "| citation hit rate | 1/1 (100 %) |" in capsys.readouterr().out
        assert '"hit": true' in out.read_text()


LIMIT = "claude did not answer: You've hit your 5-hour limit · resets 5:30pm (UTC)"
TWO = """\
- id: q1
  question: a?
  expected:
    - path: spec/a.adoc
      lines: 1-9
- id: q2
  question: b?
  expected: not_covered
"""


class Turns:
    """A session that gives one answer or error for each question, in order."""

    def __init__(self, *answers: Answer | Exception) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def ask(self, question: str) -> Answer:
        self.asked.append(question)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def __enter__(self) -> "Turns":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


def _hit() -> Answer:
    return _answer(_chunk("spec/a.adoc", 2, 4))


def _refusal() -> Answer:
    return _answer(covered=False)


def _history(path: Path) -> list[dict]:
    return [json.loads(t) for t in path.read_text().splitlines()]


class Harness:
    """Run main() on a question set with a fake session for a tree."""

    @pytest.fixture()
    def setup(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(ev, "load_settings", lambda start=None: Settings())
        monkeypatch.setattr(ev, "revision", lambda: {"sha": "abc1234", "dirty": False})
        questions = tmp_path / "questions.yaml"
        questions.write_text(TWO)
        tree = TREE

        def main(session: Turns, *extra: str) -> int:
            monkeypatch.setattr(ev, "Ish", lambda path, *, settings: session)
            return ev.main([str(questions), str(tree), *MODELS, *extra])

        return main, ev.history_file()


class TestUsageLimit(Harness):
    def test_a_limit_is_known_by_its_words(self) -> None:
        row = ev.score(TestScore.covered, FakeIsh(RuntimeError(LIMIT)), TREE)
        assert row.limited
        weekly = "You've hit your weekly limit · resets Nov 20, 3pm (UTC)"
        assert ev.score(TestScore.covered, FakeIsh(RuntimeError(weekly)), TREE).limited
        assert not ev.score(
            TestScore.covered, FakeIsh(RuntimeError("down")), TREE
        ).limited

    def test_the_run_stops_and_counts_no_miss(self, setup, capsys) -> None:
        main, history = setup
        session = Turns(RuntimeError(LIMIT), _refusal())
        assert main(session) == 1
        assert session.asked == ["a?"]
        out = capsys.readouterr().out
        assert out.startswith("Incomplete: a model usage limit stopped the run at q1:")
        assert "0 of 2 answers are scored." in out
        assert "| citation hit rate | - |" in out
        [line] = _history(history)
        assert (line["complete"], line["rounds"]) == (False, 0)
        assert line["stopped"].startswith("q1: claude did not answer")

    def test_the_rows_before_the_limit_are_scored(self, setup, capsys) -> None:
        main, history = setup
        assert main(Turns(_hit(), RuntimeError(LIMIT))) == 1
        out = capsys.readouterr().out
        assert "1 of 2 answers are scored." in out
        assert "| errors | 0 |" in out
        [line] = _history(history)
        assert line["scores"]["hit_rate"] == 1.0
        assert "refused_rate" not in line["scores"]

    def test_an_other_error_is_still_a_miss(self, setup, capsys) -> None:
        main, history = setup
        assert main(Turns(RuntimeError("down"), _refusal())) == 0
        assert "| citation hit rate | 0/1 (0 %) |" in capsys.readouterr().out
        [line] = _history(history)
        assert line["complete"] and line["scores"]["errors"] == 1


class TestRepeat(Harness):
    def test_each_round_asks_the_whole_set(self, setup, capsys) -> None:
        main, history = setup
        session = Turns(_hit(), _refusal(), _refusal(), _refusal())
        assert main(session, "--repeat", "2") == 0
        assert session.asked == ["a?", "b?", "a?", "b?"]
        out = capsys.readouterr().out
        assert "| q1 #1 | answer | True | True |" in out
        assert "| q1 #2 | answer | False | False |" in out
        assert "Over 2 rounds:" in out
        assert "| citation hit rate | 50 % | 0 % | 100 % |" in out
        [line] = _history(history)
        assert (line["repeat"], line["rounds"]) == (2, 2)
        assert line["spread"]["hit_rate"] == [0.0, 1.0]

    def test_a_short_last_round_is_left_out(self, setup, capsys) -> None:
        main, history = setup
        session = Turns(_hit(), _refusal(), _refusal(), RuntimeError(LIMIT))
        assert main(session, "--repeat", "2") == 1
        assert "Over 1 rounds:" in capsys.readouterr().out
        [line] = _history(history)
        assert line["rounds"] == 1 and line["scores"]["hit_rate"] == 1.0

    def test_a_repeat_of_none_is_refused(self, setup, capsys) -> None:
        main, _ = setup
        with pytest.raises(SystemExit):
            main(Turns(), "--repeat", "0")
        assert "--repeat takes a number of 1 or more." in capsys.readouterr().err


class TestTheReportWithNoNewOption(Harness):
    def test_the_report_is_the_table_and_the_summary(self, setup, capsys) -> None:
        main, _ = setup
        assert main(Turns(_hit(), _refusal())) == 0
        out = capsys.readouterr().out
        rows = [
            ev.Scored("q1", True, covered=True, hit=True, cited=["a.adoc:2-4"]),
            ev.Scored("q2", False),
        ]
        expected = ev.table(rows, ev.summary(rows, 0, 0))
        seconds = re.compile(r"\| \d+\.\d \|")
        assert seconds.sub("| s |", out.strip()) == seconds.sub("| s |", expected)


class TestHistory(Harness):
    def test_one_line_for_each_run(self, setup) -> None:
        main, history = setup
        main(Turns(_hit(), _refusal()), "--label", "base", "--ask-turns", "3")
        main(Turns(_hit(), _refusal()))
        first, second = _history(history)
        assert first["label"] == "base" and second["label"] == ""
        assert first["ish"] == {"sha": "abc1234", "dirty": False}
        assert first["options"]["ask_turns"] == 3
        assert first["options"]["answer_model"] == "claude-haiku-5-5"
        assert "out" not in first["options"]
        assert first["questions"]["ids"] == ["q1", "q2"]
        assert first["questions"]["sha256"] == hashlib.sha256(TWO.encode()).hexdigest()
        assert first["scores"]["hit_rate"] == 1.0
        assert first["scores"]["refused_rate"] == 1.0
        assert datetime.fromisoformat(first["time"]).tzinfo is not None

    def test_the_scores_of_an_answer(self) -> None:
        answer = Answer(
            parts=(AnswerPart("x", (_chunk("spec/a.adoc", 2, 4),)),),
            covered=True,
            sources=(),
            input_tokens=100,
            rerank_input_tokens=20,
            check_input_tokens=30,
            claims_checked=4,
            unconfirmed=(AnswerPart("y"), AnswerPart("z")),
            reads=3,
            cache_write_tokens=60,
            cache_read_tokens=25,
        )
        row = ev.score(TestScore.covered, FakeIsh(answer), TREE)
        found = ev.measures([row])
        assert (found["cache_write_tokens"], found["cache_read_tokens"]) == (60, 25)
        assert (row.unconfirmed, row.claims_checked, row.reads) == (2, 4, 3)
        assert found["unconfirmed"] == 2
        assert found["all_input_tokens"] == 150
        assert found["reads"] == 3

    def test_the_revision_of_a_repository(self, tmp_path) -> None:
        git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q"], check=True)
        (tmp_path / "f").write_text("a")
        subprocess.run([*git, "add", "f"], check=True)
        subprocess.run([*git, "commit", "-qm", "c"], check=True)
        clean = ev.revision(tmp_path)
        assert len(str(clean["sha"])) == 40 and clean["dirty"] is False
        (tmp_path / "f").write_text("b")
        assert ev.revision(tmp_path)["dirty"] is True

    def test_no_revision_outside_a_repository(self, tmp_path) -> None:
        assert ev.revision(tmp_path) == {"sha": "", "dirty": False}


def _line(label: str, rounds: int, **scores: object) -> dict:
    spread = {k: v for k, v in scores.items() if isinstance(v, list)}
    means = {
        k: (statistics.mean(v) if isinstance(v, list) else v) for k, v in scores.items()
    }
    return {
        "time": "2026-11-16T10:00:00+00:00",
        "label": label,
        "ish": {"sha": "abcdef0123", "dirty": label == "b"},
        "options": {"answer_model": "sonnet", "rerank_model": "haiku", "ask_turns": 3},
        "questions": {"sha256": "x", "ids": ["q1"]},
        "rounds": rounds,
        "complete": True,
        "stopped": "",
        "scores": means,
        "spread": spread,
    }


class TestCompare:
    def test_a_delta_is_real_only_past_the_spread(self) -> None:
        a = _line("a", 2, hit_rate=[0.8, 0.9], input_tokens=[100000, 104000])
        b = _line("b", 2, hit_rate=[0.95, 1.0], input_tokens=[101000, 103000])
        out = ev.compare((1, a), (2, b))
        assert out.splitlines()[0].startswith("A: line 1 'a', 2026-11-16")
        assert "ish abcdef0," in out and "ish abcdef0+dirty" in out
        assert "answer/rerank/turns sonnet/haiku/3, 2 round(s), complete" in out
        assert "| citation hit rate | 85 % | 98 % | +12 % | 10 % | better |" in out
        assert "| median input tokens | 102000 | 102000 | 0 | 4000 | same |" in out
        assert out.index("citation hit rate") < out.index("median input tokens")

    def test_a_delta_inside_the_spread_is_named(self) -> None:
        a = _line("a", 2, seconds=[50, 70])
        b = _line("b", 2, seconds=[60, 70])
        assert "| +5 | 20 | worse, within spread |" in ev.compare((1, a), (2, b))

    def test_one_round_has_no_spread(self) -> None:
        a = _line("a", 1, cost_usd=0.43)
        b = _line("b", 1, cost_usd=0.21)
        out = ev.compare((1, a), (2, b))
        assert "| 0.4300 | 0.2100 | -0.2200 | - | better, no spread |" in out

    def test_a_measure_one_run_lacks(self) -> None:
        a = _line("a", 1, refused_rate=1.0)
        b = _line("b", 1)
        assert "| refused when not covered | 100 % | - | - | - | - |" in ev.compare(
            (1, a), (2, b)
        )

    def test_the_differences_of_the_runs_are_named(self) -> None:
        a = _line("a", 1)
        b = {**_line("b", 1), "complete": False, "stopped": "q3: limit"}
        b["questions"] = {"sha256": "y", "ids": ["q1"]}
        out = ev.compare((1, a), (2, b))
        assert "The two runs asked different questions." in out
        assert "Run B is incomplete." in out
        assert "stopped: q3: limit" in out

    @pytest.mark.parametrize(
        ("name", "number"), [("1", 1), ("3", 3), ("-1", 3), ("-3", 1), ("base", 2)]
    )
    def test_a_line_by_number_or_label(self, name, number) -> None:
        lines = [{"label": "base"}, {"label": "base"}, {"label": ""}]
        assert ev.pick(lines, name)[0] == number

    @pytest.mark.parametrize(
        ("name", "message"),
        [("0", "no line 0"), ("4", "no line 4"), ("-4", "no line -4"), ("x", "'x'")],
    )
    def test_a_line_that_is_not_there(self, name, message) -> None:
        with pytest.raises(ValueError, match=message):
            ev.pick([{"label": ""}] * 3, name)

    def test_main_compares_two_lines_and_asks_nothing(self, capsys) -> None:
        path = ev.history_file()
        ev.append(_line("a", 1, hit_rate=0.9), path)
        ev.append(_line("b", 1, hit_rate=1.0), path)
        assert ev.main(["--compare", "a", "-1"]) == 0
        assert "| citation hit rate | 90 % | 100 % | +10 % |" in capsys.readouterr().out

    def test_main_with_no_history(self, capsys) -> None:
        with pytest.raises(SystemExit) as stopped:
            ev.main(["--compare", "1", "2"])
        assert stopped.value.code == 2
        assert "holds no history yet." in capsys.readouterr().err


class TestArguments:
    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            ([], "Give QUESTIONS, or --compare A B."),
            (["q.yaml", "tree"], "Give --language-model and --answer-model."),
        ],
    )
    def test_a_run_needs_its_inputs(self, argv, message, capsys) -> None:
        with pytest.raises(SystemExit):
            ev.main(argv)
        assert message in capsys.readouterr().err
