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

    def test_prototype_is_chunked(self, parser) -> None:
        """A header is mostly declarations, so they are its content."""
        assert symbols(parser, "int later(int a);\n") == ["later"]

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


class TestDeclarations:
    """Verify header content, which is declarations rather than bodies."""

    HEADER = (
        "/* Compute a checksum. */\n"          # 1
        "unsigned checksum(const char *d);\n"  # 2
        "\n"                                   # 3
        "class Widget {\n"                     # 4
        "public:\n"                            # 5
        "    /// Draw the widget.\n"           # 6
        "    void draw(Surface& s) const;\n"   # 7
        "    int width() const;\n"             # 8
        "    virtual ~Widget();\n"             # 9
        "private:\n"                           # 10
        "    int width_;\n"                    # 11
        "    Surface* surface_;\n"             # 12
        "};\n"                                 # 13
    )

    def test_a_header_is_more_than_one_chunk(self, parser) -> None:
        """The whole class as a single blob is not searchable enough."""
        assert len(parser.parse(Path("w.h"), self.HEADER)) == 5

    def test_free_function_declaration(self, parser) -> None:
        assert "checksum" in symbols(parser, self.HEADER, Path("w.h"))

    def test_method_declarations_are_qualified(self, parser) -> None:
        found = symbols(parser, self.HEADER, Path("w.h"))
        assert "Widget::draw" in found
        assert "Widget::width" in found

    def test_destructor_is_found(self, parser) -> None:
        assert "Widget::~Widget" in symbols(parser, self.HEADER, Path("w.h"))

    def test_data_members_are_not_chunked(self, parser) -> None:
        """A field carries nothing to search for."""
        found = symbols(parser, self.HEADER, Path("w.h"))
        assert "Widget::width_" not in found
        assert "Widget::surface_" not in found

    def test_declaration_keeps_its_doc_comment(self, parser) -> None:
        chunks = parser.parse(Path("w.h"), self.HEADER)
        draw = next(c for c in chunks if c.symbol == "Widget::draw")
        assert draw.start_line == 6
        assert "Draw the widget" in draw.text

    def test_declaration_kind(self, parser) -> None:
        chunks = parser.parse(Path("w.h"), self.HEADER)
        draw = next(c for c in chunks if c.symbol == "Widget::draw")
        assert draw.kind == "declaration"

    def test_a_variable_is_not_a_declaration_chunk(self, parser) -> None:
        assert symbols(parser, "static int counter = 0;\n") == []


class TestRedundantDeclarations:
    """Verify that a file does not list a function twice."""

    def test_prototype_yields_to_its_definition(self, parser) -> None:
        source = "int gcd(int a, int b);\n\nint gcd(int a, int b) { return a; }\n"
        chunks = parser.parse(Path("m.c"), source)
        assert [c.symbol for c in chunks] == ["gcd"]
        assert chunks[0].kind == "function"

    def test_an_undefined_prototype_survives(self, parser) -> None:
        source = "void defined(void) {}\nvoid only_declared(void);\n"
        assert symbols(parser, source, Path("m.c")) == ["defined", "only_declared"]

    def test_declaration_in_a_header_is_kept(self, parser) -> None:
        """Nothing defines it here, so it is the only record of the API."""
        assert symbols(parser, "void api(void);\n", Path("a.h")) == ["api"]


class TestMethodKind:
    """Verify that a definition inside a class reads as a method."""

    SOURCE = "class A {\npublic:\n  void run() { }\n  bool done() const;\n};\n"

    def test_inline_definition_is_a_method(self, parser) -> None:
        chunks = parser.parse(Path("a.hpp"), self.SOURCE)
        run = next(c for c in chunks if c.symbol == "A::run")
        assert run.kind == "method"

    def test_free_function_stays_a_function(self, parser) -> None:
        chunks = parser.parse(SRC, "void loose(void) {}\n")
        assert chunks[0].kind == "function"
