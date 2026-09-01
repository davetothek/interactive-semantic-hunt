"""Parse source with a Tree-sitter grammar.

One configurable parser serves every Tree-sitter language. A flavor names
the grammar, the suffixes it claims, which node types become chunks, and
which of those types nest their children.

Tree-sitter is error tolerant: a file it cannot fully parse still yields
a tree with ERROR nodes. Return whatever was recognized, and raise only
when nothing was, so a partly broken file stays searchable.
"""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ish.application.ports.parser import ParseError
from ish.domain.chunk import Chunk

log = logging.getLogger(__name__)

# Node types that carry the name of the definition they belong to.
_NAME_TYPES = frozenset(
    {
        "identifier",
        "type_identifier",
        "field_identifier",
        "qualified_identifier",
        "namespace_identifier",
        "destructor_name",
        "operator_name",
        "primitive_type",
    }
)

# Fields to follow when hunting for the name of a declaration.
_NAME_FIELDS = ("name", "declarator")


class TreeSitterParser:
    """Extract chunks from source using a Tree-sitter grammar."""

    def __init__(
        self,
        *,
        language: str,
        suffixes: frozenset[str],
        grammar: Any,
        kinds: Mapping[str, str],
        containers: frozenset[str],
        needs_body: frozenset[str] = frozenset(),
        separator: str = "::",
    ) -> None:
        self.language = language
        self.suffixes = suffixes
        self._kinds = dict(kinds)
        self._containers = containers
        self._needs_body = needs_body
        self._separator = separator
        self._grammar = grammar
        self._parser: Any = None

    def _ensure_parser(self) -> Any:
        """Build the underlying parser on first use."""
        if self._parser is None:
            from tree_sitter import Language, Parser

            self._parser = Parser(Language(self._grammar))
        return self._parser

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Return a chunk for every recognized definition."""
        data = source.encode("utf-8")
        tree = self._ensure_parser().parse(data)
        lines = source.splitlines(keepends=True)

        chunks: list[Chunk] = []
        self._visit(tree.root_node, path, lines, data, [], chunks)

        if not chunks and tree.root_node.has_error:
            raise ParseError(f"No definition could be read from {path}")
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _visit(
        self,
        node: Any,
        path: Path,
        lines: list[str],
        data: bytes,
        trail: list[str],
        chunks: list[Chunk],
    ) -> None:
        """Walk the tree, emitting a chunk for each recognized node."""
        for child in node.children:
            kind = self._kinds.get(child.type)
            if kind is None:
                self._visit(child, path, lines, data, trail, chunks)
                continue

            # A type named without a body is a reference, not a definition.
            # `struct Node *next;` must not become a second Node chunk.
            if (
                child.type in self._needs_body
                and child.child_by_field_name("body") is None
            ):
                continue

            name = self._name_of(child, data)
            symbol = self._separator.join([*trail, name]) if name else None

            start = self._start_line(child)
            end = child.end_point[0] + 1
            chunks.append(
                Chunk(
                    path=path,
                    text="".join(lines[start - 1 : end]),
                    kind=kind,
                    language=self.language,
                    symbol=symbol,
                    start_line=start,
                    end_line=end,
                )
            )

            # Recurse into a container so its members get a qualified name.
            if child.type in self._containers:
                deeper = [*trail, name] if name else list(trail)
                self._visit(child, path, lines, data, deeper, chunks)

    @staticmethod
    def _start_line(node: Any) -> int:
        """Return the first line of *node*, an attached comment included.

        A doc comment names what the code does, which is exactly the
        context a search needs, so keep it with the definition.
        """
        start = node.start_point[0] + 1
        sibling = node.prev_sibling
        while sibling is not None and sibling.type == "comment":
            if sibling.end_point[0] + 1 != start - 1:
                break
            start = sibling.start_point[0] + 1
            sibling = sibling.prev_sibling
        return start

    def _name_of(self, node: Any, data: bytes) -> str:
        """Find the identifier that names *node*."""
        found = self._descend(node)
        if found is None:
            return ""
        return data[found.start_byte : found.end_byte].decode("utf-8", "replace")

    def _descend(self, node: Any) -> Any:
        """Follow the naming fields down to an identifier."""
        for field in _NAME_FIELDS:
            child = node.child_by_field_name(field)
            if child is None:
                continue
            if child.type in _NAME_TYPES:
                return child
            found = self._descend(child)
            if found is not None:
                return found
        return None


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
        },
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
