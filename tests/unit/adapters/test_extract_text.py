"""Test the source-slicing helpers of the Python parser adapter."""

import ast

from ish.adapters.parser.python import _extract_text, _start_line


def test_extract_text_slices_inclusive_range():
    lines = ["a\n", "b\n", "c\n"]
    assert _extract_text(lines, 1, 2) == "a\nb\n"
    assert _extract_text(lines, 3, 3) == "c\n"


def test_start_line_without_decorator():
    node = ast.parse("def f():\n    pass\n").body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _start_line(node) == 1


def test_start_line_with_decorators():
    node = ast.parse("@one\n@two\ndef f():\n    pass\n").body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _start_line(node) == 1
