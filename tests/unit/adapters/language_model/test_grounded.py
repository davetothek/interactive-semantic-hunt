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


def test_the_system_prompt_asks_for_paragraphs() -> None:
    assert "short paragraphs" in _grounded.SYSTEM_PROMPT
    assert "two newline characters" in _grounded.SYSTEM_PROMPT


def test_the_system_prompt_asks_for_the_layout() -> None:
    for part in ("summary", "sections", "steps", "table", "gaps", "at most five"):
        assert part in _grounded.SYSTEM_PROMPT


def _reply(**fields) -> str:
    """Return the JSON of an answer, with the fields not given left empty."""
    return json.dumps(
        {
            "covered": True,
            "summary": {"text": "", "source_ids": []},
            "sections": [],
            "gaps": [],
            **fields,
        }
    )


def _section(style="prose", heading="", passages=(), columns=(), rows=()):
    return {
        "heading": heading,
        "style": style,
        "passages": list(passages),
        "columns": list(columns),
        "rows": list(rows),
    }


class TestParse:
    def test_the_layout_and_the_flat_passages_follow_reading_order(self) -> None:
        text = _reply(
            summary={"text": "Short.", "source_ids": ["1"]},
            sections=[
                _section("prose", "Why", [{"text": "Because", "source_ids": ["1", 2]}]),
                _section(
                    "table",
                    "Registers",
                    [],
                    ["Name", "Bits"],
                    [{"cells": ["ERR_MODE", "25:24"], "source_ids": ["2"]}],
                ),
                _section("steps", "How", [{"text": "Set it.", "source_ids": []}]),
            ],
            gaps=[{"text": "INCK is open.", "source_ids": ["3"]}],
        )
        reply = _grounded.parse(text, "m", input_tokens=10, output_tokens=3)
        assert reply.covered
        assert [b.kind for b in reply.blocks] == [
            "summary",
            "prose",
            "table",
            "steps",
            "gaps",
        ]
        table = reply.blocks[2]
        assert (table.heading, table.columns) == ("Registers", ("Name", "Bits"))
        assert table.rows[0].cells == ("ERR_MODE", "25:24")
        assert table.rows[0].source_ids == ("2",)
        assert [(p.text, p.source_ids) for p in reply.passages] == [
            ("Short.", ("1",)),
            ("Because", ("1", "2")),
            ("ERR_MODE | 25:24", ("2",)),
            ("Set it.", ()),
            ("INCK is open.", ("3",)),
        ]
        assert (reply.input_tokens, reply.output_tokens) == (10, 3)

    def test_a_table_with_no_rows_is_prose(self) -> None:
        reply = _grounded.parse(
            _reply(sections=[_section("table", "", [{"text": "x", "source_ids": []}])]),
            "m",
        )
        assert reply.blocks[0].kind == "prose"

    def test_an_unknown_style_is_prose(self) -> None:
        reply = _grounded.parse(
            _reply(sections=[_section("poem", "", [{"text": "x", "source_ids": []}])]),
            "m",
        )
        assert reply.blocks[0].kind == "prose"

    def test_empty_parts_are_dropped(self) -> None:
        reply = _grounded.parse(
            _reply(
                summary={"text": "  ", "source_ids": []},
                sections=[
                    _section("prose", "Empty", [{"text": " ", "source_ids": []}])
                ],
                gaps=[{"text": "", "source_ids": []}],
            ),
            "m",
        )
        assert (reply.blocks, reply.passages, reply.covered) == ((), (), False)

    def test_rows_belong_only_to_a_table(self) -> None:
        reply = _grounded.parse(
            _reply(
                sections=[
                    _section(
                        "prose",
                        "",
                        [{"text": "x", "source_ids": []}],
                        rows=[{"cells": ["a"], "source_ids": []}],
                    )
                ]
            ),
            "m",
        )
        assert reply.blocks[0].rows == ()

    def test_missing_parts_read_as_empty(self) -> None:
        assert not _grounded.parse('{"covered": true}', "m").covered

    def test_not_covered(self) -> None:
        assert not _grounded.parse(_reply(covered=False), "m").covered

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            '{"sections": []}',
            '{"covered": true, "sections": 3}',
            '{"covered": true, "summary": "text"}',
            '{"covered": true, "gaps": [{"source_ids": []}]}',
            '{"covered": true, "sections": [{"rows": [{"source_ids": []}]}]}',
            "[]",
        ],
    )
    def test_the_wrong_shape_names_the_backend(self, text: str) -> None:
        with pytest.raises(LanguageModelError, match="backend-x did not reply"):
            _grounded.parse(text, "backend-x")

    def test_a_covered_value_that_is_not_a_boolean(self) -> None:
        with pytest.raises(LanguageModelError, match="covered='yes'"):
            _grounded.parse('{"covered": "yes"}', "m")


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


class TestStep:
    def test_the_step_prompt_names_the_searches_run_and_the_turns_left(self) -> None:
        sources = [Source("1", "/d/a.adoc:1-9 A", "Alpha.")]
        text = _grounded.step_prompt("Why?", sources, ["first query"], 2)
        assert text.startswith(_grounded.prompt("Why?", sources))
        assert "Searches already run:\n- first query" in text
        assert text.endswith("Turns left for searches: 2")
        assert "- none" in _grounded.step_prompt("Why?", sources, [], 0)

    def test_the_step_prompt_extends_the_answer_prompt(self) -> None:
        assert _grounded.STEP_PROMPT.startswith(_grounded.SYSTEM_PROMPT)
        assert "up to three new search queries" in _grounded.STEP_PROMPT
        assert _grounded.STEP_SCHEMA["required"] == [
            "covered",
            "summary",
            "sections",
            "gaps",
            "search",
        ]

    def test_a_step_with_searches(self) -> None:
        reply = _grounded.parse_step(
            '{"covered": false, "search": [" a ", "", "b"]}',
            "m",
            4,
            1,
        )
        assert reply.searches == ("a", "b")
        assert (reply.input_tokens, reply.output_tokens) == (4, 1)

    def test_a_step_that_answers(self) -> None:
        reply = _grounded.parse_step(
            _reply(summary={"text": "x", "source_ids": ["1"]}, search=[]), "m"
        )
        assert reply.covered and reply.searches == ()
        assert reply.blocks[0].kind == "summary"

    def test_a_step_with_no_search_field(self) -> None:
        reply = _grounded.parse_step('{"covered": false}', "m")
        assert reply.searches == ()
