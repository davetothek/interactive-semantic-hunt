"""Test the shared Markdown and AsciiDoc parser."""

from pathlib import Path

import pytest

from ish.adapters.parser._sections import SectionParser
from ish.adapters.parser.asciidoc import AsciiDocParser
from ish.adapters.parser.markdown import markdown_parser
from ish.application.ports.parser import Parser

MD = Path("doc.md")
ADOC = Path("doc.adoc")


@pytest.fixture()
def markdown() -> SectionParser:
    return markdown_parser()


@pytest.fixture()
def asciidoc() -> SectionParser:
    return AsciiDocParser()


class TestIdentity:
    """Verify each flavor satisfies the port and claims its suffixes."""

    def test_both_satisfy_the_port(
        self, markdown: SectionParser, asciidoc: SectionParser
    ) -> None:
        assert isinstance(markdown, Parser)
        assert isinstance(asciidoc, Parser)

    def test_markdown_suffixes(self, markdown: SectionParser) -> None:
        assert markdown.language == "markdown"
        assert ".md" in markdown.suffixes

    def test_asciidoc_suffixes(self, asciidoc: SectionParser) -> None:
        assert asciidoc.language == "asciidoc"
        assert asciidoc.suffixes == {".adoc", ".asciidoc", ".asc"}

    def test_flavors_claim_different_suffixes(
        self, markdown: SectionParser, asciidoc: SectionParser
    ) -> None:
        """Registering both must not raise a suffix conflict."""
        assert not (markdown.suffixes & asciidoc.suffixes)


class TestMarkdown:
    """Verify section extraction from Markdown."""

    SOURCE = (
        "# Guide\n"  # 1
        "\n"  # 2
        "Intro text.\n"  # 3
        "\n"  # 4
        "## Install\n"  # 5
        "\n"  # 6
        "Run uv sync.\n"  # 7
        "\n"  # 8
        "### From source\n"  # 9
        "\n"  # 10
        "Clone it.\n"  # 11
        "\n"  # 12
        "## Usage\n"  # 13
        "\n"  # 14
        "Run ish.\n"  # 15
    )

    def test_one_chunk_per_heading(self, markdown: SectionParser) -> None:
        assert len(markdown.parse(MD, self.SOURCE)) == 4

    def test_symbols_carry_the_heading_path(self, markdown: SectionParser) -> None:
        symbols = [c.symbol for c in markdown.parse(MD, self.SOURCE)]
        assert symbols == [
            "Guide",
            "Guide > Install",
            "Guide > Install > From source",
            "Guide > Usage",
        ]

    def test_a_sibling_pops_the_deeper_trail(self, markdown: SectionParser) -> None:
        """Usage follows a level-3 heading but belongs under Guide."""
        last = markdown.parse(MD, self.SOURCE)[-1]
        assert last.symbol == "Guide > Usage"

    def test_top_level_is_a_document(self, markdown: SectionParser) -> None:
        kinds = [c.kind for c in markdown.parse(MD, self.SOURCE)]
        assert kinds == ["document", "section", "section", "section"]

    def test_section_runs_to_the_next_heading(self, markdown: SectionParser) -> None:
        install = markdown.parse(MD, self.SOURCE)[1]
        assert install.start_line == 5
        assert install.end_line == 8
        assert "Run uv sync." in install.text

    def test_last_section_runs_to_the_end(self, markdown: SectionParser) -> None:
        assert markdown.parse(MD, self.SOURCE)[-1].end_line == 15

    def test_language_is_stamped(self, markdown: SectionParser) -> None:
        assert {c.language for c in markdown.parse(MD, self.SOURCE)} == {"markdown"}


class TestAsciiDoc:
    """Verify the same behavior with the AsciiDoc marker."""

    SOURCE = "= Title\n\nLead.\n\n== Chapter\n\nBody.\n\n=== Detail\n\nMore.\n"

    def test_sections_are_found(self, asciidoc: SectionParser) -> None:
        symbols = [c.symbol for c in asciidoc.parse(ADOC, self.SOURCE)]
        assert symbols == ["Title", "Title > Chapter", "Title > Chapter > Detail"]

    def test_language_is_stamped(self, asciidoc: SectionParser) -> None:
        assert {c.language for c in asciidoc.parse(ADOC, self.SOURCE)} == {"asciidoc"}

    def test_markdown_headings_are_not_asciidoc(self, asciidoc: SectionParser) -> None:
        """The line is prose to this flavor, so the file is one document."""
        chunks = asciidoc.parse(ADOC, "# Not a heading here\n")
        assert [(c.kind, c.symbol) for c in chunks] == [("document", "doc")]


class TestFencedBlocks:
    """Verify that source inside a fence is not read as a heading."""

    def test_markdown_comment_in_a_fence(self, markdown: SectionParser) -> None:
        source = (
            "# Real\n\n```python\n# not a heading\n## also not\n```\n\n## Also real\n"
        )
        symbols = [c.symbol for c in markdown.parse(MD, source)]
        assert symbols == ["Real", "Real > Also real"]

    def test_tilde_fence(self, markdown: SectionParser) -> None:
        source = "# Real\n\n~~~\n# hidden\n~~~\n"
        assert len(markdown.parse(MD, source)) == 1

    def test_asciidoc_listing_block(self, asciidoc: SectionParser) -> None:
        source = "= Real\n\n----\n= hidden\n----\n\n== Also real\n"
        symbols = [c.symbol for c in asciidoc.parse(ADOC, source)]
        assert symbols == ["Real", "Real > Also real"]

    def test_unclosed_fence_hides_the_rest(self, markdown: SectionParser) -> None:
        """An unterminated fence must not crash the parse."""
        source = "# Real\n\n```\n# hidden\n"
        assert [c.symbol for c in markdown.parse(MD, source)] == ["Real"]


class TestEdgeCases:
    """Verify documents that carry no usable structure."""

    def test_empty_document(self, markdown: SectionParser) -> None:
        assert markdown.parse(MD, "") == []

    def test_heading_needs_a_space(self, markdown: SectionParser) -> None:
        """A bare #hashtag is not a heading. It is a line of text."""
        chunks = markdown.parse(MD, "#hashtag\n")
        assert [(c.kind, c.symbol) for c in chunks] == [("document", "doc")]

    def test_heading_needs_a_title(self, markdown: SectionParser) -> None:
        """A marker with no title is neither a heading nor prose."""
        assert markdown.parse(MD, "##   \n") == []

    def test_document_starting_at_a_deep_level(self, markdown: SectionParser) -> None:
        """A file whose first heading is level 3 still parses."""
        chunks = markdown.parse(MD, "### Deep\n\nText.\n")
        assert chunks[0].symbol == "Deep"
        assert chunks[0].kind == "section"

    def test_path_is_stamped(self, markdown: SectionParser) -> None:
        assert markdown.parse(Path("x/y.md"), "# T\n")[0].path == Path("x/y.md")


class TestHeadinglessDocuments:
    """Verify a file with no heading yields one chunk when it holds prose.

    ish chunks by section, so a file with no heading made no chunks and
    still entered the files table, where it looked indexed. On one tree
    2,844 files held no chunks. A sample of 400 was all attribute
    definitions, include shells, and register table rows, for which
    nothing is right. A paragraph of prose would have disappeared with
    no signal.
    """

    def test_prose_is_one_document_named_after_the_file(
        self, asciidoc: SectionParser
    ) -> None:
        source = "The bus arbiter grants the first request.\nIt holds it.\n"
        chunks = asciidoc.parse(Path("spec/arbiter.adoc"), source)
        assert len(chunks) == 1
        assert chunks[0].kind == "document"
        assert chunks[0].symbol == "arbiter"
        assert chunks[0].text == source
        assert (chunks[0].start_line, chunks[0].end_line) == (1, 2)
        assert chunks[0].language == "asciidoc"

    def test_markdown_prose_too(self, markdown: SectionParser) -> None:
        chunks = markdown.parse(MD, "Just prose.\nMore prose.\n")
        assert [c.symbol for c in chunks] == ["doc"]

    def test_attribute_definitions_are_nothing(self, asciidoc: SectionParser) -> None:
        source = ":project: Widget\n:revnumber!:\n:toc-title: Contents\n"
        assert asciidoc.parse(ADOC, source) == []

    def test_an_include_shell_is_nothing(self, asciidoc: SectionParser) -> None:
        source = "include::a.adoc[]\n\ninclude::b.adoc[leveloffset=+1]\n"
        assert asciidoc.parse(ADOC, source) == []

    def test_table_rows_are_nothing(self, asciidoc: SectionParser) -> None:
        source = "[cols=2*]\n|===\n| REG_A | 0x00\n| REG_B | 0x04\n|===\n"
        assert asciidoc.parse(ADOC, source) == []

    def test_conditionals_and_comments_are_nothing(
        self, asciidoc: SectionParser
    ) -> None:
        source = "ifdef::internal[]\n// a note to the writer\nendif::[]\n"
        assert asciidoc.parse(ADOC, source) == []

    def test_an_html_comment_is_nothing(self, markdown: SectionParser) -> None:
        assert markdown.parse(MD, "<!-- generated -->\n") == []

    def test_fenced_code_alone_is_nothing(self, markdown: SectionParser) -> None:
        assert markdown.parse(MD, "```\nx = 1\n```\n") == []

    def test_one_line_of_prose_among_machinery_is_enough(
        self, asciidoc: SectionParser
    ) -> None:
        source = ":project: Widget\ninclude::a.adoc[]\n\nRead this first.\n"
        assert len(asciidoc.parse(ADOC, source)) == 1

    def test_a_heading_still_wins(self, asciidoc: SectionParser) -> None:
        chunks = asciidoc.parse(ADOC, "= Title\n\nProse.\n")
        assert [c.symbol for c in chunks] == ["Title"]
