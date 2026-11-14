"""Test the scoring of the ask evaluation script.

The script lives outside the package, because it measures ish rather
than ships with it. Load it by path.
"""

import importlib.util
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
