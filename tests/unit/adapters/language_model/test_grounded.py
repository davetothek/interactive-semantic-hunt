"""Test the prompt and the reply parser for models with no citation feature."""

import json

import pytest

from ish.adapters.language_model import _grounded
from ish.application.ports.language_model import LanguageModelError, Source


def test_the_prompt_puts_each_source_under_its_id_before_the_question() -> None:
    sources = [
        Source("1", "/d/a.adoc:1-9 A", "Alpha."),
        Source("2", "/d/b.adoc:3-4", "Beta."),
    ]
    assert _grounded.prompt("Why?", sources) == (
        "[1] /d/a.adoc:1-9 A\nAlpha.\n\n[2] /d/b.adoc:3-4\nBeta.\n\nQuestion: Why?"
    )


def test_the_system_prompt_forbids_outside_knowledge() -> None:
    assert "only the sources" in _grounded.SYSTEM_PROMPT
    assert "covered to false" in _grounded.SYSTEM_PROMPT


class TestParse:
    def test_passages_and_their_ids(self) -> None:
        text = json.dumps(
            {
                "covered": True,
                "passages": [
                    {"text": "It boots", "source_ids": ["1", 2]},
                    {"text": ".", "source_ids": []},
                ],
            }
        )
        reply = _grounded.parse(text, "m", input_tokens=10, output_tokens=3)
        assert reply.covered
        assert [(p.text, p.source_ids) for p in reply.passages] == [
            ("It boots", ("1", "2")),
            (".", ()),
        ]
        assert (reply.input_tokens, reply.output_tokens) == (10, 3)

    def test_covered_with_no_passages_is_not_covered(self) -> None:
        reply = _grounded.parse('{"covered": true, "passages": []}', "m")
        assert not reply.covered

    def test_not_covered(self) -> None:
        assert not _grounded.parse('{"covered": false, "passages": []}', "m").covered

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            '{"passages": []}',
            '{"covered": true}',
            '{"covered": true, "passages": [{"source_ids": []}]}',
            '{"covered": true, "passages": 3}',
            "[]",
        ],
    )
    def test_the_wrong_shape_names_the_backend(self, text: str) -> None:
        with pytest.raises(LanguageModelError, match="backend-x did not reply"):
            _grounded.parse(text, "backend-x")

    def test_a_covered_value_that_is_not_a_boolean(self) -> None:
        with pytest.raises(LanguageModelError, match="covered='yes'"):
            _grounded.parse('{"covered": "yes", "passages": []}', "m")


class TestSelect:
    def test_the_prompt_lists_candidates_then_the_limit_and_question(self) -> None:
        candidates = [Source("1", "/d/a.adoc:1-9 A", "Start of A")]
        assert _grounded.select_prompt("Why?", candidates, 10) == (
            "[1] /d/a.adoc:1-9 A\nStart of A\n\nChoose at most 10.\n\nQuestion: Why?"
        )

    def test_the_prompt_prefers_a_rule_over_a_test(self) -> None:
        assert "test case" in _grounded.SELECT_PROMPT
        assert "bare numbers" in _grounded.SELECT_PROMPT

    def test_ids_keep_their_order_drop_repeats_and_stop_at_the_limit(self) -> None:
        selection = _grounded.parse_ids(
            '{"ids": ["3", 1, "3", "2", "5"]}', "m", 3, input_tokens=9, output_tokens=2
        )
        assert selection.ids == ("3", "1", "2")
        assert (selection.input_tokens, selection.output_tokens) == (9, 2)

    @pytest.mark.parametrize("text", ["nope", "{}", '{"ids": 3}'])
    def test_the_wrong_shape_names_the_backend(self, text: str) -> None:
        with pytest.raises(LanguageModelError, match="backend-y did not reply"):
            _grounded.parse_ids(text, "backend-y", 5)
