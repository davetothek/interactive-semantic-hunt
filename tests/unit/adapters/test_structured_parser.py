"""Test the YAML and JSON parser."""

from pathlib import Path

import pytest

from ish.adapters.parser.structured import StructuredParser
from ish.application.ports.parser import ParseError, Parser

YML = Path("spec.yaml")
JSN = Path("data.json")


@pytest.fixture()
def yaml_parser() -> StructuredParser:
    return StructuredParser.yaml()


@pytest.fixture()
def json_parser() -> StructuredParser:
    return StructuredParser.json()


class TestIdentity:
    """Verify both flavors satisfy the port and claim their suffixes."""

    def test_satisfy_the_port(self, yaml_parser, json_parser) -> None:
        assert isinstance(yaml_parser, Parser)
        assert isinstance(json_parser, Parser)

    def test_yaml_suffixes(self, yaml_parser) -> None:
        assert yaml_parser.suffixes == {".yaml", ".yml"}
        assert yaml_parser.language == "yaml"

    def test_json_suffixes(self, json_parser) -> None:
        assert json_parser.suffixes == {".json"}
        assert json_parser.language == "json"

    def test_flavors_do_not_collide(self, yaml_parser, json_parser) -> None:
        assert not (yaml_parser.suffixes & json_parser.suffixes)


class TestWholeDocument:
    """Verify the document becomes one chunk."""

    SOURCE = "metadata:\n  description: A test\ncases:\n  - purpose: one\n"

    def test_one_chunk(self, yaml_parser) -> None:
        assert len(yaml_parser.parse(YML, self.SOURCE)) == 1

    def test_text_is_the_whole_file(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, self.SOURCE)[0].text == self.SOURCE

    def test_line_range_covers_the_file(self, yaml_parser) -> None:
        chunk = yaml_parser.parse(YML, self.SOURCE)[0]
        assert (chunk.start_line, chunk.end_line) == (1, 4)

    def test_kind_and_language(self, yaml_parser) -> None:
        chunk = yaml_parser.parse(YML, self.SOURCE)[0]
        assert chunk.kind == "document"
        assert chunk.language == "yaml"


class TestNaming:
    """Verify the document names itself when it can."""

    def test_nested_metadata_description(self, yaml_parser) -> None:
        """The shape this parser was built for."""
        source = "metadata:\n  description: Smoke test for flash\ncases: []\n"
        assert yaml_parser.parse(YML, source)[0].symbol == "Smoke test for flash"

    def test_top_level_title(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, "title: My Doc\nx: 1\n")[0].symbol == "My Doc"

    def test_name_is_preferred_over_description(self, yaml_parser) -> None:
        source = "name: Short\ndescription: Much longer text\n"
        assert yaml_parser.parse(YML, source)[0].symbol == "Short"

    def test_falls_back_to_the_file_stem(self, yaml_parser) -> None:
        chunk = yaml_parser.parse(Path("dir/settings.yaml"), "a: 1\n")[0]
        assert chunk.symbol == "settings"

    def test_a_blank_title_is_not_used(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, "title: '   '\na: 1\n")[0].symbol == "spec"

    def test_a_non_string_title_is_not_used(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, "name: 42\n")[0].symbol == "spec"

    def test_a_sequence_document_uses_the_stem(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, "- one\n- two\n")[0].symbol == "spec"


class TestJson:
    """Verify JSON, which YAML is a superset of."""

    def test_object_is_parsed(self, json_parser) -> None:
        source = '{"name": "config", "value": 1}\n'
        chunk = json_parser.parse(JSN, source)[0]
        assert chunk.symbol == "config"
        assert chunk.language == "json"

    def test_array_is_parsed(self, json_parser) -> None:
        assert len(json_parser.parse(JSN, "[1, 2, 3]\n")) == 1

    def test_nested_object(self, json_parser) -> None:
        source = '{"metadata": {"description": "A thing"}}\n'
        assert json_parser.parse(JSN, source)[0].symbol == "A thing"


class TestFailures:
    """Verify that a broken document is reported, not embedded."""

    def test_malformed_yaml_raises(self, yaml_parser) -> None:
        with pytest.raises(ParseError):
            yaml_parser.parse(YML, "key: [unclosed\n  bad: : :\n")

    def test_malformed_json_raises(self, json_parser) -> None:
        with pytest.raises(ParseError):
            json_parser.parse(JSN, '{"a": [1, 2,,]}\n')

    def test_the_reason_is_one_line(self, yaml_parser) -> None:
        """A multi-line reason would break the per-file warning."""
        with pytest.raises(ParseError) as exc_info:
            yaml_parser.parse(YML, "a: [\nb: :\n")
        assert "\n" not in str(exc_info.value)

    def test_empty_file(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, "") == []

    def test_whitespace_only(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, "\n  \n") == []

    def test_comments_only_is_not_an_error(self, yaml_parser) -> None:
        """A valid document that holds nothing yields no chunk to search."""
        chunks = yaml_parser.parse(YML, "# just a comment\n")
        assert chunks[0].symbol == "spec"
