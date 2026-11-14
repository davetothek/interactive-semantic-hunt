"""Test the prompt and the reply parser for models with no citation feature."""

import json

import pytest

from ish.adapters.language_model import _grounded
from ish.adapters.language_model._prompts import load as load_prompts
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
        text = " ".join(PROMPTS.select.split())
        assert "over a test that only exercises it" in text
        assert "bare numbers" in text

    def test_the_prompt_also_chooses_the_code_and_the_test_it_needs(self) -> None:
        text = " ".join(PROMPTS.select.split())
        assert "A title starts with the type of its source" in text
        assert "Also choose the code that does what the question asks" in text

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
            "terms",
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


class TestTerms:
    """The acronyms an answer uses arrive with what they stand for."""

    def _reply(self, terms: list) -> str:
        return json.dumps(
            {
                "covered": True,
                "summary": {"text": "AE runs.", "source_ids": ["1"]},
                "sections": [],
                "gaps": [],
                "terms": terms,
            }
        )

    def test_the_schema_asks_for_terms(self) -> None:
        assert "terms" in _grounded.SCHEMA["required"]

    def test_each_term_is_read_with_its_sources(self) -> None:
        reply = _grounded.parse(
            self._reply(
                [{"term": " AE ", "meaning": "Auto Exposure ", "source_ids": [1]}]
            ),
            "m",
        )
        (term,) = reply.terms
        assert (term.term, term.meaning, term.source_ids) == (
            "AE",
            "Auto Exposure",
            ("1",),
        )

    def test_a_step_carries_its_terms(self) -> None:
        data = json.loads(
            self._reply([{"term": "AE", "meaning": "x", "source_ids": []}])
        )
        reply = _grounded.parse_step(json.dumps({**data, "search": []}), "m")
        assert [t.term for t in reply.terms] == ["AE"]

    def test_the_prompt_never_lets_a_term_be_invented(self) -> None:
        assert "never expand a term from your own knowledge" in PROMPTS.answer


class TestQuery:
    def test_the_prompt_holds_the_history_and_the_question(self) -> None:
        assert _grounded.query_prompt("and it?", "Q: a\nA: b") == (
            "Earlier turns:\n\nQ: a\nA: b\n\nNew question: and it?"
        )

    def test_a_query_keeps_one_line(self) -> None:
        query = _grounded.parse_query('{"query": " watchdog \\n reset "}', "m", 9, 2)
        assert (query.text, query.input_tokens, query.output_tokens) == (
            "watchdog reset",
            9,
            2,
        )

    @pytest.mark.parametrize("text", ["nope", "{}", "[]"])
    def test_the_wrong_shape_names_the_backend(self, text: str) -> None:
        with pytest.raises(LanguageModelError, match="m did not reply with a search"):
            _grounded.parse_query(text, "m")

    def test_an_empty_query_is_an_error(self) -> None:
        with pytest.raises(LanguageModelError, match="m wrote an empty search query"):
            _grounded.parse_query('{"query": "  "}', "m")

    def test_the_shipped_instruction_asks_for_a_short_query(self) -> None:
        text = " ".join(PROMPTS.query.split())
        assert "at most 15 words" in text and "Do not answer the question" in text


class TestBlockStream:
    """Each block streams as the final parse builds it, in reading order."""

    ANSWER = {
        "covered": True,
        "summary": {"text": "It resets.", "source_ids": ["1"]},
        "sections": [
            {
                "heading": "How",
                "style": "steps",
                "passages": [{"text": "Write CTRL.", "source_ids": ["2"]}],
                "columns": [],
                "rows": [],
            },
            {
                "heading": "Empty",
                "style": "prose",
                "passages": [],
                "columns": [],
                "rows": [],
            },
            {
                "heading": "Values",
                "style": "table",
                "passages": [],
                "columns": ["Register", "Reset"],
                "rows": [{"cells": ["CTRL", "0x0"], "source_ids": ["1"]}],
            },
        ],
        "gaps": [{"text": "No source names the delay.", "source_ids": []}],
        "terms": [],
    }

    def _streamed(self, answer: dict, size: int = 7) -> list:
        blocks: list = []
        stream = _grounded.BlockStream(blocks.append)
        text = json.dumps(answer)
        for start in range(0, len(text), size):
            stream.feed(text[start : start + size])
        return blocks

    def test_the_streamed_blocks_are_the_blocks_of_the_reply(self) -> None:
        reply = _grounded.parse(json.dumps(self.ANSWER), "m")
        assert tuple(self._streamed(self.ANSWER)) == reply.blocks
        assert [b.kind for b in reply.blocks] == ["summary", "steps", "table", "gaps"]

    def test_an_empty_summary_streams_nothing_and_the_sections_still_do(self) -> None:
        answer = {**self.ANSWER, "summary": {"text": " ", "source_ids": []}}
        assert [b.kind for b in self._streamed(answer)] == ["steps", "table", "gaps"]

    def test_sections_before_the_summary_stream_nothing(self) -> None:
        answer = {
            "covered": True,
            "sections": self.ANSWER["sections"],
            "summary": self.ANSWER["summary"],
            "gaps": [],
            "terms": [],
        }
        assert self._streamed(answer) == []

    def test_gaps_before_the_sections_stop_the_stream(self) -> None:
        answer = {
            "covered": True,
            "summary": self.ANSWER["summary"],
            "gaps": self.ANSWER["gaps"],
            "sections": self.ANSWER["sections"],
            "terms": [],
        }
        assert [b.kind for b in self._streamed(answer)] == ["summary"]

    def test_a_section_of_the_wrong_shape_stops_the_stream(self) -> None:
        broken = {"heading": "Bad", "style": "table", "rows": [{"cells": 3}]}
        answer = {**self.ANSWER, "sections": [broken, *self.ANSWER["sections"]]}
        assert [b.kind for b in self._streamed(answer)] == ["summary"]
