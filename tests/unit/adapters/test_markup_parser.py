"""Test the shared Markdown and AsciiDoc parser."""

from pathlib import Path

import pytest

from ish.adapters.parser.markup import MarkupParser
from ish.application.ports.parser import Parser

MD = Path("doc.md")
ADOC = Path("doc.adoc")


@pytest.fixture()
def markdown() -> MarkupParser:
    return MarkupParser.markdown()


@pytest.fixture()
def asciidoc() -> MarkupParser:
    return MarkupParser.asciidoc()


class TestIdentity:
    """Verify each flavor satisfies the port and claims its suffixes."""

    def test_both_satisfy_the_port(
        self, markdown: MarkupParser, asciidoc: MarkupParser
    ) -> None:
        assert isinstance(markdown, Parser)
        assert isinstance(asciidoc, Parser)

    def test_markdown_suffixes(self, markdown: MarkupParser) -> None:
        assert markdown.language == "markdown"
        assert ".md" in markdown.suffixes

    def test_asciidoc_suffixes(self, asciidoc: MarkupParser) -> None:
        assert asciidoc.language == "asciidoc"
        assert asciidoc.suffixes == {".adoc", ".asciidoc", ".asc"}

    def test_flavors_claim_different_suffixes(
        self, markdown: MarkupParser, asciidoc: MarkupParser
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

    def test_one_chunk_per_heading(self, markdown: MarkupParser) -> None:
        assert len(markdown.parse(MD, self.SOURCE)) == 4

    def test_symbols_carry_the_heading_path(self, markdown: MarkupParser) -> None:
        symbols = [c.symbol for c in markdown.parse(MD, self.SOURCE)]
        assert symbols == [
            "Guide",
            "Guide > Install",
            "Guide > Install > From source",
            "Guide > Usage",
        ]

    def test_a_sibling_pops_the_deeper_trail(self, markdown: MarkupParser) -> None:
        """Usage follows a level-3 heading but belongs under Guide."""
        last = markdown.parse(MD, self.SOURCE)[-1]
        assert last.symbol == "Guide > Usage"

    def test_top_level_is_a_document(self, markdown: MarkupParser) -> None:
        kinds = [c.kind for c in markdown.parse(MD, self.SOURCE)]
        assert kinds == ["document", "section", "section", "section"]

    def test_section_runs_to_the_next_heading(self, markdown: MarkupParser) -> None:
        install = markdown.parse(MD, self.SOURCE)[1]
        assert install.start_line == 5
        assert install.end_line == 8
        assert "Run uv sync." in install.text

    def test_last_section_runs_to_the_end(self, markdown: MarkupParser) -> None:
        assert markdown.parse(MD, self.SOURCE)[-1].end_line == 15

    def test_language_is_stamped(self, markdown: MarkupParser) -> None:
        assert {c.language for c in markdown.parse(MD, self.SOURCE)} == {"markdown"}


class TestAsciiDoc:
    """Verify the same behavior with the AsciiDoc marker."""

    SOURCE = "= Title\n\nLead.\n\n== Chapter\n\nBody.\n\n=== Detail\n\nMore.\n"

    def test_sections_are_found(self, asciidoc: MarkupParser) -> None:
        symbols = [c.symbol for c in asciidoc.parse(ADOC, self.SOURCE)]
        assert symbols == ["Title", "Title > Chapter", "Title > Chapter > Detail"]

    def test_language_is_stamped(self, asciidoc: MarkupParser) -> None:
        assert {c.language for c in asciidoc.parse(ADOC, self.SOURCE)} == {"asciidoc"}

    def test_markdown_headings_are_not_asciidoc(
        self, asciidoc: MarkupParser
    ) -> None:
        assert asciidoc.parse(ADOC, "# Not a heading here\n") == []


class TestFencedBlocks:
    """Verify that source inside a fence is not read as a heading."""

    def test_markdown_comment_in_a_fence(self, markdown: MarkupParser) -> None:
        source = (
            "# Real\n"
            "\n"
            "```python\n"
            "# not a heading\n"
            "## also not\n"
            "```\n"
            "\n"
            "## Also real\n"
        )
        symbols = [c.symbol for c in markdown.parse(MD, source)]
        assert symbols == ["Real", "Real > Also real"]

    def test_tilde_fence(self, markdown: MarkupParser) -> None:
        source = "# Real\n\n~~~\n# hidden\n~~~\n"
        assert len(markdown.parse(MD, source)) == 1

    def test_asciidoc_listing_block(self, asciidoc: MarkupParser) -> None:
        source = "= Real\n\n----\n= hidden\n----\n\n== Also real\n"
        symbols = [c.symbol for c in asciidoc.parse(ADOC, source)]
        assert symbols == ["Real", "Real > Also real"]

    def test_unclosed_fence_hides_the_rest(self, markdown: MarkupParser) -> None:
        """An unterminated fence must not crash the parse."""
        source = "# Real\n\n```\n# hidden\n"
        assert [c.symbol for c in markdown.parse(MD, source)] == ["Real"]


class TestEdgeCases:
    """Verify documents that carry no usable structure."""

    def test_no_headings(self, markdown: MarkupParser) -> None:
        assert markdown.parse(MD, "Just prose.\nMore prose.\n") == []

    def test_empty_document(self, markdown: MarkupParser) -> None:
        assert markdown.parse(MD, "") == []

    def test_heading_needs_a_space(self, markdown: MarkupParser) -> None:
        """A bare #hashtag is not a heading."""
        assert markdown.parse(MD, "#hashtag\n") == []

    def test_heading_needs_a_title(self, markdown: MarkupParser) -> None:
        assert markdown.parse(MD, "##   \n") == []

    def test_document_starting_at_a_deep_level(self, markdown: MarkupParser) -> None:
        """A file whose first heading is level 3 still parses."""
        chunks = markdown.parse(MD, "### Deep\n\nText.\n")
        assert chunks[0].symbol == "Deep"
        assert chunks[0].kind == "section"

    def test_path_is_stamped(self, markdown: MarkupParser) -> None:
        assert markdown.parse(Path("x/y.md"), "# T\n")[0].path == Path("x/y.md")
