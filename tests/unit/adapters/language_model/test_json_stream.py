"""Test the scanner that finds each JSON object and array as it arrives."""

import json
import random

from ish.adapters.language_model._json_stream import JsonValues

DOCUMENT = {
    "covered": True,
    "summary": {
        "text": 'It is "quoted", with \\ and [brackets] {braces}',
        "ids": ["1"],
    },
    "sections": [{"heading": "A", "rows": [{"cells": ["x", "y"]}]}, {"heading": "B"}],
    "gaps": [],
}


def _values(text: str, cuts: list[int]) -> list[tuple[tuple, object]]:
    scanner, found, start = JsonValues(), [], 0
    for end in [*cuts, len(text)]:
        found += list(scanner.feed(text[start:end]))
        start = end
    return found


class TestJsonValues:
    def test_each_value_comes_whole_with_its_path_wherever_the_text_is_cut(
        self,
    ) -> None:
        text = json.dumps(DOCUMENT)
        rng = random.Random(3)
        for _ in range(50):
            cuts = sorted(rng.sample(range(1, len(text)), 12))
            found = dict(_values(text, cuts))
            assert found[("summary",)] == DOCUMENT["summary"]
            assert found[("sections", 0, "rows", 0)] == {"cells": ["x", "y"]}
            assert found[("sections", 1)] == {"heading": "B"}
            assert found[("gaps",)] == []
            assert found[()] == DOCUMENT

    def test_a_value_comes_at_the_character_that_closes_it(self) -> None:
        scanner = JsonValues()
        assert list(scanner.feed('{"a": {"b": 1')) == []
        assert list(scanner.feed("}")) == [(("a",), {"b": 1})]
        assert list(scanner.feed("}")) == [((), {"a": {"b": 1}})]

    def test_a_string_holds_no_structure(self) -> None:
        text = '{"k": "a } ] \\" , : still", "n": [1, 2]}'
        assert dict(_values(text, [5, 9, 13])) == {
            ("n",): [1, 2],
            (): {"k": 'a } ] " , : still', "n": [1, 2]},
        }

    def test_a_closing_bracket_with_nothing_open_is_left(self) -> None:
        assert list(JsonValues().feed("] }")) == []
