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
    """Verify a document describing one thing stays one chunk.

    A mapping is the attributes of a single thing. Splitting it would
    scatter that thing across several vectors.
    """

    SOURCE = "name: A test\nversion: 3\nenabled: true\n"

    def test_one_chunk(self, yaml_parser) -> None:
        assert len(yaml_parser.parse(YML, self.SOURCE)) == 1

    def test_text_is_the_whole_file(self, yaml_parser) -> None:
        assert yaml_parser.parse(YML, self.SOURCE)[0].text == self.SOURCE

    def test_line_range_covers_the_file(self, yaml_parser) -> None:
        chunk = yaml_parser.parse(YML, self.SOURCE)[0]
        assert (chunk.start_line, chunk.end_line) == (1, 3)

    def test_kind_and_language(self, yaml_parser) -> None:
        chunk = yaml_parser.parse(YML, self.SOURCE)[0]
        assert chunk.kind == "document"
        assert chunk.language == "yaml"

    def test_a_list_of_plain_values_is_not_a_list_of_things(
        self, yaml_parser
    ) -> None:
        """Tags belong to the document, not to themselves."""
        source = "name: A test\ntags:\n  - one\n  - two\n"
        assert len(yaml_parser.parse(YML, source)) == 1


class TestListOfThings:
    """Verify a document listing several things becomes several chunks.

    Measured on a real specification corpus: one chunk per test case
    rather than per file moved top-one retrieval from 30% to 90%.
    """

    SOURCE = (
        "metadata:\n"
        "  description: A test\n"
        "cases:\n"
        "  - purpose: reads the flash\n"
        "    step: one\n"
        "  - purpose: writes the flash\n"
        "    step: two\n"
    )

    def test_one_chunk_per_entry(self, yaml_parser) -> None:
        chunks = yaml_parser.parse(YML, self.SOURCE)
        assert len(chunks) == 3

    def test_each_entry_names_itself(self, yaml_parser) -> None:
        symbols = [c.symbol for c in yaml_parser.parse(YML, self.SOURCE)]
        assert any("reads the flash" in s for s in symbols)
        assert any("writes the flash" in s for s in symbols)

    def test_the_document_title_leads_each_name(self, yaml_parser) -> None:
        chunks = yaml_parser.parse(YML, self.SOURCE)
        assert all(c.symbol.startswith("A test") for c in chunks)

    def test_splitting_stops_at_the_things(self, yaml_parser) -> None:
        """An entry's own fields must not become chunks of their own."""
        source = (
            "cases:\n"
            "  - purpose: one\n"
            "    steps:\n"
            "      - do: a\n"
            "      - do: b\n"
            "  - purpose: two\n"
            "    steps:\n"
            "      - do: c\n"
        )
        chunks = yaml_parser.parse(YML, source)
        assert len(chunks) == 2

    def test_no_content_is_lost(self, yaml_parser) -> None:
        chunks = yaml_parser.parse(YML, self.SOURCE)
        covered: set[int] = set()
        for c in chunks:
            covered.update(range(c.start_line, c.end_line + 1))
        carries = {
            n
            for n, line in enumerate(self.SOURCE.splitlines(), 1)
            if any(ch.isalnum() for ch in line)
        }
        assert carries <= covered


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


class TestOversizedDocuments:
    """Verify a document too large to embed is split, not truncated.

    An embedding model reads a fixed number of tokens and drops the
    rest without saying so, which left most of a large document
    unsearchable while looking indexed.
    """

    def _big(self, cases: int, filler: int) -> str:
        body = "".join(
            f"  - purpose: case number {i}\n    body: {'x' * filler}\n"
            for i in range(cases)
        )
        return f"metadata:\n  description: A large spec\ncases:\n{body}"

    def test_a_small_document_with_no_entries_stays_whole(
        self, yaml_parser
    ) -> None:
        """Size alone must not divide a document that describes one thing."""
        chunks = yaml_parser.parse(YML, "name: small\nvalue: 1\n")
        assert len(chunks) == 1
        assert chunks[0].kind == "document"

    def test_a_large_document_is_split(self, yaml_parser) -> None:
        chunks = yaml_parser.parse(YML, self._big(cases=40, filler=900))
        assert len(chunks) > 1

    def test_no_piece_exceeds_the_limit(self, yaml_parser) -> None:
        from ish.adapters.parser.structured import MAX_CHUNK_CHARS

        chunks = yaml_parser.parse(YML, self._big(cases=40, filler=900))
        assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)

    def test_no_content_is_lost(self, yaml_parser) -> None:
        """Splitting must not lose content, which is the bug it fixes.

        Structural punctuation may fall between pieces. A line carrying
        a word may not.
        """
        source = self._big(cases=40, filler=900)
        chunks = yaml_parser.parse(YML, source)

        covered: set[int] = set()
        for c in chunks:
            covered.update(range(c.start_line, c.end_line + 1))

        carries_content = {
            n
            for n, line in enumerate(source.splitlines(), 1)
            if any(ch.isalnum() for ch in line)
        }
        assert carries_content <= covered

    def test_the_container_key_is_kept(self, yaml_parser) -> None:
        """The key naming a split container must land in its first part."""
        chunks = yaml_parser.parse(YML, self._big(cases=40, filler=900))
        assert any("cases:" in c.text for c in chunks)

    def test_each_piece_names_itself(self, yaml_parser) -> None:
        chunks = yaml_parser.parse(YML, self._big(cases=40, filler=900))
        symbols = [c.symbol for c in chunks]
        assert len(set(symbols)) == len(symbols)
        assert any("case number 7" in s for s in symbols)

    def test_the_document_title_leads_every_name(self, yaml_parser) -> None:
        chunks = yaml_parser.parse(YML, self._big(cases=40, filler=900))
        assert all(c.symbol.startswith("A large spec") for c in chunks)

    def test_a_split_piece_is_a_section(self, yaml_parser) -> None:
        chunks = yaml_parser.parse(YML, self._big(cases=40, filler=900))
        assert {c.kind for c in chunks} == {"section"}

    def test_an_unsplittable_value_is_reported(self, yaml_parser, caplog) -> None:
        """One enormous scalar cannot be divided, so say so."""
        source = f"metadata:\n  description: Huge\nblob: {'x' * 40000}\n"
        with caplog.at_level("WARNING"):
            chunks = yaml_parser.parse(YML, source)
        assert chunks
        assert "cannot be split" in caplog.text

    def test_a_large_json_document_is_split(self, json_parser) -> None:
        import json as json_module

        payload = {
            "name": "big",
            "items": [{"name": f"item {i}", "data": "y" * 900} for i in range(40)],
        }
        chunks = json_parser.parse(JSN, json_module.dumps(payload, indent=2))
        assert len(chunks) > 1


class TestUnnamedParts:
    """Verify a part with nothing to name it still gets an identity."""

    def test_a_sequence_of_values_is_numbered(self, yaml_parser) -> None:
        entries = "".join(f"  - {'v' * 900}\n" for _ in range(40))
        chunks = yaml_parser.parse(YML, f"name: Plain\nitems:\n{entries}")

        assert len(chunks) > 1
        assert any(c.symbol.endswith("[0]") for c in chunks)

    def test_a_mapping_without_a_title_key_uses_its_index(self, yaml_parser) -> None:
        entries = "".join(f"  - value: {'v' * 900}\n" for _ in range(40))
        chunks = yaml_parser.parse(YML, f"name: Plain\nitems:\n{entries}")
        assert any("[1]" in c.symbol for c in chunks)
