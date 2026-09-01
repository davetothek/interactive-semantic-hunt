"""Test the PythonParser adapter against known source strings."""

from pathlib import Path

import pytest

from ish.adapters.parser.python import PythonParser
from ish.application.ports.parser import ParseError

DUMMY_PATH = Path("test.py")


@pytest.fixture()
def parser() -> PythonParser:
    """Provide a fresh parser instance for each test."""
    return PythonParser()


# ------------------------------------------------------------------
# Single-definition cases
# ------------------------------------------------------------------


class TestTopLevelFunction:
    """Verify extraction of a plain top-level function."""

    SOURCE = "def greet():\n    return 'hello'\n"

    def test_kind(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert chunks[0].kind == "function"

    def test_symbol(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert chunks[0].symbol == "greet"

    def test_count(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert len(chunks) == 1


class TestAsyncFunction:
    """Verify extraction of a top-level async function."""

    SOURCE = "async def fetch():\n    pass\n"

    def test_kind(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert chunks[0].kind == "async_function"

    def test_symbol(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert chunks[0].symbol == "fetch"


class TestClass:
    """Verify extraction of a class definition."""

    SOURCE = "class Client:\n    pass\n"

    def test_kind(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert chunks[0].kind == "class"

    def test_symbol(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert chunks[0].symbol == "Client"


class TestMethod:
    """Verify extraction of a regular method inside a class."""

    SOURCE = "class Client:\n    def connect(self):\n        pass\n"

    def test_kind(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        method = next(c for c in chunks if c.kind == "method")
        assert method.kind == "method"

    def test_qualified_name(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        method = next(c for c in chunks if c.kind == "method")
        assert method.symbol == "Client.connect"


class TestAsyncMethod:
    """Verify extraction of an async method inside a class."""

    SOURCE = "class Client:\n    async def request(self):\n        pass\n"

    def test_kind(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        method = next(c for c in chunks if c.kind == "async_method")
        assert method.kind == "async_method"

    def test_qualified_name(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        method = next(c for c in chunks if c.kind == "async_method")
        assert method.symbol == "Client.request"


# ------------------------------------------------------------------
# Multi-definition and composite cases
# ------------------------------------------------------------------


class TestMultipleDefinitions:
    """Verify that the parser handles a file with many definitions."""

    SOURCE = (
        "class Client:\n"
        "    def connect(self) -> None:\n"
        '        print("connected")\n'
        "\n"
        "\n"
        "def load_config() -> dict[str, str]:\n"
        "    return {}\n"
    )

    def test_total_count(self, parser: PythonParser) -> None:
        """Expect: class, method, function."""
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert len(chunks) == 3

    def test_kinds(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        kinds = [c.kind for c in chunks]
        assert "class" in kinds
        assert "method" in kinds
        assert "function" in kinds

    def test_symbols(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        symbols = [c.symbol for c in chunks]
        assert "Client" in symbols
        assert "Client.connect" in symbols
        assert "load_config" in symbols


# ------------------------------------------------------------------
# Line numbers
# ------------------------------------------------------------------


class TestLineNumbers:
    """Verify 1-based inclusive start/end line numbers."""

    SOURCE = (
        "def first():\n"  # line 1
        "    pass\n"  # line 2
        "\n"  # line 3
        "\n"  # line 4
        "def second():\n"  # line 5
        "    pass\n"  # line 6
    )

    def test_first_start(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        first = next(c for c in chunks if c.symbol == "first")
        assert first.start_line == 1

    def test_first_end(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        first = next(c for c in chunks if c.symbol == "first")
        assert first.end_line == 2

    def test_second_start(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        second = next(c for c in chunks if c.symbol == "second")
        assert second.start_line == 5

    def test_second_end(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        second = next(c for c in chunks if c.symbol == "second")
        assert second.end_line == 6


# ------------------------------------------------------------------
# Source text extraction
# ------------------------------------------------------------------


class TestSourceTextExtraction:
    """Verify that the extracted text matches the original source."""

    SOURCE = "def greet(name: str) -> str:\n    return f'hello {name}'\n"

    def test_text_matches(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        assert chunks[0].text == self.SOURCE

    def test_method_text_preserves_indent(self, parser: PythonParser) -> None:
        source = "class Foo:\n    def bar(self):\n        return 1\n"
        chunks = parser.parse(DUMMY_PATH, source)
        method = next(c for c in chunks if c.kind == "method")
        assert method.text == "    def bar(self):\n        return 1\n"


# ------------------------------------------------------------------
# Syntax error handling
# ------------------------------------------------------------------


class TestSyntaxErrorHandling:
    """Verify that invalid syntax surfaces as ``ParseError``."""

    def test_raises_on_broken_function(self, parser: PythonParser) -> None:
        with pytest.raises(ParseError):
            parser.parse(DUMMY_PATH, "def broken(:\n")

    def test_raises_on_broken_class(self, parser: PythonParser) -> None:
        with pytest.raises(ParseError):
            parser.parse(DUMMY_PATH, "class broken(:\n    pass\n")

    def test_chains_the_native_error(self, parser: PythonParser) -> None:
        with pytest.raises(ParseError) as exc_info:
            parser.parse(DUMMY_PATH, "def broken(:\n")
        assert isinstance(exc_info.value.__cause__, SyntaxError)


# ------------------------------------------------------------------
# Decorators
# ------------------------------------------------------------------


class TestDecorators:
    """Verify that decorators belong to the chunk they decorate."""

    SOURCE = (
        "@app.route('/users')\n"  # line 1
        "@cached\n"  # line 2
        "def users():\n"  # line 3
        "    return []\n"  # line 4
        "\n"  # line 5
        "@final\n"  # line 6
        "class Registry:\n"  # line 7
        "    @property\n"  # line 8
        "    def size(self):\n"  # line 9
        "        return 0\n"  # line 10
    )

    def test_function_starts_at_first_decorator(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        func = next(c for c in chunks if c.symbol == "users")
        assert func.start_line == 1

    def test_function_text_includes_decorators(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        func = next(c for c in chunks if c.symbol == "users")
        assert func.text.startswith("@app.route('/users')\n@cached\n")

    def test_class_starts_at_decorator(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        cls = next(c for c in chunks if c.symbol == "Registry")
        assert cls.start_line == 6
        assert cls.text.startswith("@final\n")

    def test_method_includes_decorator(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, self.SOURCE)
        method = next(c for c in chunks if c.symbol == "Registry.size")
        assert method.start_line == 8
        assert method.text.startswith("    @property\n")


# ------------------------------------------------------------------
# Path propagation
# ------------------------------------------------------------------


class TestPathPropagation:
    """Verify that the parser stamps each chunk with the correct path."""

    def test_path_set(self, parser: PythonParser) -> None:
        path = Path("src/app/models.py")
        chunks = parser.parse(path, "def f(): pass\n")
        assert chunks[0].path == path


class TestLanguageStamp:
    """Verify that every chunk carries its source language."""

    def test_chunks_are_stamped(self, parser: PythonParser) -> None:
        chunks = parser.parse(DUMMY_PATH, "class A:\n    def b(self): pass\n")
        assert {c.language for c in chunks} == {"python"}

    def test_parser_declares_its_identity(self, parser: PythonParser) -> None:
        assert parser.language == "python"
        assert ".py" in parser.suffixes

    def test_stub_files_are_claimed(self, parser: PythonParser) -> None:
        assert ".pyi" in parser.suffixes
