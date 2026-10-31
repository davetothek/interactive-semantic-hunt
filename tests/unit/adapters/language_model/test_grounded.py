"""Test the prompt and the reply parser for models with no citation feature."""

import json

import pytest

from ish.adapters.language_model import _grounded
from ish.adapters.language_model.prompts import load as load_prompts
from ish.application.ports.language_model import LanguageModelError, Source

PROMPTS = load_prompts()


def test_the_prompt_puts_each_source_under_its_id_before_the_question() -> None:
    sources = [
        Source("1", "/d/a.adoc:1-9 A", "Alpha."),
        Source("2", "/d/b.adoc:3-4", "Beta."),
    ]
    assert _grounded.prompt("Why?", sources) == (
        "[1] /d/a.adoc:1-9 A\nAlpha.\n\n[2] /d/b.adoc:3-4\nBeta.\n\nQuestion: Why?"
    )


def test_the_shipped_answer_instruction_keeps_its_rules() -> None:
    for part in (
        "only the sources",
        "covered to false",
        "short paragraphs",
        "two newline characters",
        "summary",
        "sections",
        "steps",
        "table",
        "gaps",
        "at most five",
    ):
        assert part in PROMPTS.answer


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
                    [{"cells": ["MODE_REG", "25:24"], "source_ids": ["2"]}],
                ),
                _section("steps", "How", [{"text": "Set it.", "source_ids": []}]),
            ],
            gaps=[{"text": "CLK_IN is open.", "source_ids": ["3"]}],
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
        assert table.rows[0].cells == ("MODE_REG", "25:24")
        assert table.rows[0].source_ids == ("2",)
        assert [(p.text, p.source_ids) for p in reply.passages] == [
            ("Short.", ("1",)),
            ("Because", ("1", "2")),
            ("MODE_REG | 25:24", ("2",)),
            ("Set it.", ()),
            ("CLK_IN is open.", ("3",)),
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
        assert "test case" in PROMPTS.select
        assert "bare numbers" in PROMPTS.select

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
        assert PROMPTS.step.startswith(PROMPTS.answer)
        assert "up to three new search queries" in PROMPTS.step
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


def test_no_more_gaps_than_the_instruction_allows() -> None:
    gaps = [{"text": f"gap {n}", "source_ids": []} for n in range(9)]
    reply = _grounded.parse(_reply(gaps=gaps), "m")
    assert len(reply.blocks[0].passages) == _grounded.MAX_GAPS


def test_markers_a_model_writes_into_its_text_are_dropped() -> None:
    """A model wrote "[16][17]", the ids it saw, which mean nothing to a reader."""
    reply = _grounded.parse(
        _reply(
            sections=[
                _section(
                    "table",
                    "",
                    [{"text": "Fields [16][17] and more [2].", "source_ids": ["2"]}],
                    ["Reg"],
                    [{"cells": ["A [3]"], "source_ids": ["3"]}],
                )
            ]
        ),
        "m",
    )
    block = reply.blocks[0]
    assert block.passages[0].text == "Fields and more."
    assert block.rows[0].cells == ("A",)
    assert block.passages[0].source_ids == ("2",)
