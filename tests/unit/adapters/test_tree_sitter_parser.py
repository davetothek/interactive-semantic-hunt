"""Test the Tree-sitter parser through its C and C++ flavor."""

from pathlib import Path

import pytest

from ish.adapters.parser.tree_sitter import cpp_parser
from ish.application.ports.parser import ParseError, Parser

SRC = Path("probe.cpp")


@pytest.fixture(scope="module")
def parser():
    return cpp_parser()


def symbols(parser, source: str, path: Path = SRC) -> list[str | None]:
    return [c.symbol for c in parser.parse(path, source)]


class TestIdentity:
    """Verify the parser satisfies the port and claims both languages."""

    def test_satisfies_the_port(self, parser) -> None:
        assert isinstance(parser, Parser)

    def test_language_is_cpp(self, parser) -> None:
        assert parser.language == "cpp"

    def test_claims_c_and_cpp_suffixes(self, parser) -> None:
        for suffix in (".c", ".h", ".cpp", ".hpp", ".cc", ".cxx"):
            assert suffix in parser.suffixes

    def test_owns_the_ambiguous_header_suffix(self, parser) -> None:
        """One parser owns .h, so no conflict can arise."""
        assert ".h" in parser.suffixes


class TestFunctions:
    """Verify plain functions."""

    def test_c_function(self, parser) -> None:
        assert symbols(parser, "int gcd(int a, int b) { return a; }\n") == ["gcd"]

    def test_static_function(self, parser) -> None:
        assert symbols(parser, "static void helper(void) {}\n") == ["helper"]

    def test_pointer_return(self, parser) -> None:
        assert symbols(parser, "char *dup(const char *s) { return 0; }\n") == ["dup"]

    def test_declaration_without_a_body_is_skipped(self, parser) -> None:
        """A prototype carries no implementation to search."""
        assert symbols(parser, "int later(int a);\n") == []

    def test_line_numbers(self, parser) -> None:
        chunks = parser.parse(SRC, "\n\nint f(void) {\n  return 1;\n}\n")
        assert (chunks[0].start_line, chunks[0].end_line) == (3, 5)

    def test_language_is_stamped(self, parser) -> None:
        chunks = parser.parse(SRC, "int f(void) {}\n")
        assert chunks[0].language == "cpp"


class TestDocComments:
    """Verify that an attached comment travels with the definition."""

    def test_line_comment_is_included(self, parser) -> None:
        source = "// Add two integers.\nint add(int a, int b) { return a + b; }\n"
        chunk = parser.parse(SRC, source)[0]
        assert chunk.start_line == 1
        assert chunk.text.startswith("// Add two integers.")

    def test_block_comment_is_included(self, parser) -> None:
        source = "/* Compute a thing. */\nint go(void) { return 0; }\n"
        assert parser.parse(SRC, source)[0].start_line == 1

    def test_a_separated_comment_is_left_out(self, parser) -> None:
        """A blank line means the comment belongs to something else."""
        source = "// Unrelated note.\n\nint go(void) { return 0; }\n"
        assert parser.parse(SRC, source)[0].start_line == 3


class TestTypes:
    """Verify classes, structs, unions, and enums."""

    def test_class(self, parser) -> None:
        assert symbols(parser, "class Widget { int x; };\n") == ["Widget"]

    def test_struct(self, parser) -> None:
        assert symbols(parser, "struct Point { double x, y; };\n") == ["Point"]

    def test_union(self, parser) -> None:
        assert symbols(parser, "union Value { int i; float f; };\n") == ["Value"]

    def test_enum(self, parser) -> None:
        assert symbols(parser, "enum Color { RED, GREEN };\n") == ["Color"]

    def test_kinds_are_distinct(self, parser) -> None:
        source = "class A {};\nstruct B {};\nenum C { X };\nint d(void) {}\n"
        kinds = [c.kind for c in parser.parse(SRC, source)]
        assert kinds == ["class", "struct", "enum", "function"]

    def test_forward_declaration_is_not_a_definition(self, parser) -> None:
        assert symbols(parser, "struct Opaque;\nclass Fwd;\n") == []

    def test_a_type_reference_makes_no_second_chunk(self, parser) -> None:
        """`struct Node *next` names Node but does not define it again."""
        source = "struct Node { int v; struct Node *next; };\n"
        assert symbols(parser, source) == ["Node"]

    def test_anonymous_type_has_no_symbol(self, parser) -> None:
        chunks = parser.parse(SRC, "typedef enum { RED } Color;\n")
        assert chunks[0].symbol is None


class TestNesting:
    """Verify qualified names."""

    SOURCE = (
        "namespace geometry {\n"
        "class Point {\n"
        "public:\n"
        "  double norm() const { return 0.0; }\n"
        "};\n"
        "}\n"
    )

    def test_members_are_qualified(self, parser) -> None:
        assert "geometry::Point::norm" in symbols(parser, self.SOURCE)

    def test_class_is_qualified_by_namespace(self, parser) -> None:
        assert "geometry::Point" in symbols(parser, self.SOURCE)

    def test_namespace_itself_is_a_chunk(self, parser) -> None:
        assert "geometry" in symbols(parser, self.SOURCE)

    def test_out_of_line_definition_keeps_its_qualifier(self, parser) -> None:
        source = "double Point::norm() const { return 0.0; }\n"
        assert symbols(parser, source) == ["Point::norm"]


class TestErrorHandling:
    """Verify the error-tolerant contract."""

    def test_partial_parse_returns_what_was_read(self, parser) -> None:
        """Tree-sitter recovers, so a good definition must survive."""
        source = "int good(void) { return 1; }\n\nint bad( { { {\n"
        assert "good" in symbols(parser, source)

    def test_unreadable_source_raises(self, parser) -> None:
        with pytest.raises(ParseError):
            parser.parse(SRC, "$$$ ((( ]]] ###\n")

    def test_empty_file_is_not_an_error(self, parser) -> None:
        assert parser.parse(SRC, "") == []

    def test_comments_only_is_not_an_error(self, parser) -> None:
        assert parser.parse(SRC, "// nothing here\n") == []


class TestUnicode:
    """Verify that byte offsets and line numbers survive wide characters."""

    def test_name_after_a_unicode_comment(self, parser) -> None:
        source = "// café naïve — notes\nint after(void) { return 0; }\n"
        chunk = parser.parse(SRC, source)[0]
        assert chunk.symbol == "after"
        assert chunk.start_line == 1
