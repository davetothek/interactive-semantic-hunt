"""Implement the Python AST parser adapter.

Satisfy the ``Parser`` port defined in ``ish.application.ports.parser``.
"""

import ast
from collections.abc import Sequence
from pathlib import Path

from ish.application.ports.parser import ParseError
from ish.domain.chunk import Chunk


class PythonParser:
    """Extract semantic chunks from Python source using the ``ast`` module."""

    suffixes = frozenset({".py"})

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Parse *source* and return chunks for every recognized definition.

        Raise ``ParseError`` when *source* cannot be parsed. The caller
        decides how to report the failure.
        """
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise ParseError(str(exc)) from exc
        lines = source.splitlines(keepends=True)
        chunks: list[Chunk] = []
        self._visit_body(tree.body, path, lines, parent_name=None, chunks=chunks)
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _visit_body(
        self,
        body: list[ast.stmt],
        path: Path,
        lines: list[str],
        *,
        parent_name: str | None,
        chunks: list[Chunk],
    ) -> None:
        """Walk a list of statements and collect chunks from definitions."""
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._handle_function(node, path, lines, parent_name, chunks)
            elif isinstance(node, ast.ClassDef):
                self._handle_class(node, path, lines, parent_name, chunks)

    def _handle_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        path: Path,
        lines: list[str],
        parent_name: str | None,
        chunks: list[Chunk],
    ) -> None:
        """Emit a chunk for a function or method definition."""
        is_async = isinstance(node, ast.AsyncFunctionDef)

        if parent_name is not None:
            kind = "async_method" if is_async else "method"
            symbol = f"{parent_name}.{node.name}"
        else:
            kind = "async_function" if is_async else "function"
            symbol = node.name

        start = _start_line(node)
        chunks.append(
            Chunk(
                path=path,
                text=_extract_text(lines, start, node.end_lineno or node.lineno),
                kind=kind,
                symbol=symbol,
                start_line=start,
                end_line=node.end_lineno or node.lineno,
            )
        )

    def _handle_class(
        self,
        node: ast.ClassDef,
        path: Path,
        lines: list[str],
        parent_name: str | None,
        chunks: list[Chunk],
    ) -> None:
        """Emit a chunk for a class, then recurse into its body for methods."""
        symbol = f"{parent_name}.{node.name}" if parent_name else node.name

        start = _start_line(node)
        chunks.append(
            Chunk(
                path=path,
                text=_extract_text(lines, start, node.end_lineno or node.lineno),
                kind="class",
                symbol=symbol,
                start_line=start,
                end_line=node.end_lineno or node.lineno,
            )
        )

        # Recurse to pick up methods and nested classes.
        self._visit_body(node.body, path, lines, parent_name=symbol, chunks=chunks)


def _start_line(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    """Return the first source line of *node*, decorators included.

    ``node.lineno`` points at the ``def`` or ``class`` keyword, so a
    decorated definition starts at its first decorator instead.
    """
    if node.decorator_list:
        return min(node.lineno, node.decorator_list[0].lineno)
    return node.lineno


def _extract_text(lines: list[str], start: int, end: int) -> str:
    """Slice the original source between 1-based inclusive line numbers."""
    return "".join(lines[start - 1 : end])
