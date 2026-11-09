"""Read C and C++ with the Tree-sitter C++ grammar.

The walk lives in ``_tree_sitter.py``. This module names the node types
that become chunks, and which of them nest their children.
"""

from ish.adapters.parser._tree_sitter import TreeSitterParser


def cpp_parser() -> TreeSitterParser:
    """Build the C and C++ parser.

    One parser owns both languages. The C++ grammar reads nearly all C,
    and the two share the ``.h`` suffix, so splitting them would leave
    every header ambiguous.
    """
    import tree_sitter_cpp

    return TreeSitterParser(
        language="cpp",
        suffixes=frozenset({".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}),
        grammar=tree_sitter_cpp.language(),
        kinds={
            "function_definition": "function",
            "class_specifier": "class",
            "struct_specifier": "struct",
            "union_specifier": "union",
            "enum_specifier": "enum",
            "namespace_definition": "namespace",
            # A header is mostly declarations, so they are its content.
            "declaration": "declaration",
            "field_declaration": "declaration",
        },
        member_kinds={"function_definition": "method"},
        needs_function=frozenset({"declaration", "field_declaration"}),
        containers=frozenset(
            {"class_specifier", "struct_specifier", "namespace_definition"}
        ),
        needs_body=frozenset(
            {
                "class_specifier",
                "struct_specifier",
                "union_specifier",
                "enum_specifier",
            }
        ),
    )
