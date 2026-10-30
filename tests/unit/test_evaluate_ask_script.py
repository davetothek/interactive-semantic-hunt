"""Test the scoring of the ask evaluation script.

The script lives outside the package, because it measures ish rather
than ships with it. Load it by path.
"""

import importlib.util
from pathlib import Path

import pytest

from ish.application.answer import Answer, AnswerPart
from ish.domain.chunk import Chunk

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

    def test_an_empty_summary(self) -> None:
        numbers = ev.summary([], 0, 0)
        assert numbers["citation hit rate"] == "-"
        assert numbers["median seconds"] == 0
