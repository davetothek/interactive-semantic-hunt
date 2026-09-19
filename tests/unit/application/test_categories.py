"""Test how a chunk is sorted into code, doc, test, or config."""

from pathlib import Path

import pytest

from ish.application.categories import (
    TYPES,
    by_language,
    category_of,
    compile_categories,
)
from ish.application.filters import Filters, build_result_filter
from ish.domain.chunk import Chunk

# What the registered languages hold, as bootstrap reads it off them.
HOLDS = {
    "markdown": "doc",
    "asciidoc": "doc",
    "yaml": "config",
    "json": "config",
    "python": "code",
    "cpp": "code",
}

sort_by_language = by_language(HOLDS)


def make_chunk(path: str, language: str = "python") -> Chunk:
    return Chunk(
        path=Path(path),
        text="x",
        kind="function",
        language=language,
        symbol="x",
        start_line=1,
        end_line=1,
    )


class TestCategories:
    """Verify how a chunk is sorted into code, doc, test, or config."""

    @pytest.mark.parametrize(
        ("path", "language", "expected"),
        [
            ("/p/src/a.py", "python", "code"),
            ("/p/src/a.cpp", "cpp", "code"),
            ("/p/README.md", "markdown", "doc"),
            ("/p/doc/guide.adoc", "asciidoc", "doc"),
            ("/p/deploy.yaml", "yaml", "config"),
            ("/p/package.json", "json", "config"),
            ("/p/tests/test_a.py", "python", "test"),
            ("/p/test/a.py", "python", "test"),
            ("/p/src/a_test.py", "python", "test"),
            ("/p/conftest.py", "python", "test"),
            ("/p/spec/a.py", "python", "test"),
        ],
    )
    def test_category(self, path: str, language: str, expected: str) -> None:
        assert sort_by_language(make_chunk(path, language)) == expected

    def test_a_fixture_counts_as_a_test_not_config(self) -> None:
        """A YAML fixture belongs with the tests that read it."""
        assert sort_by_language(make_chunk("/p/tests/data/case.yaml", "yaml")) == "test"

    def test_a_doc_inside_tests_counts_as_a_test(self) -> None:
        assert sort_by_language(make_chunk("/p/tests/README.md", "markdown")) == "test"

    def test_a_language_nothing_says_anything_about_holds_code(self) -> None:
        """A user parser that declares no category reads as code."""
        assert sort_by_language(make_chunk("/p/src/a.toy", "toy")) == "code"

    def test_with_no_registry_only_the_path_speaks(self) -> None:
        """All a caller with no registry can know."""
        assert category_of(make_chunk("/p/README.md", "markdown")) == "code"
        assert category_of(make_chunk("/p/tests/a.py")) == "test"

    def test_every_category_is_listed(self) -> None:
        assert set(TYPES) == {"code", "doc", "test", "config"}


class TestConfigurableCategories:
    """Verify a repository can say what its own paths mean.

    A naming convention belongs to a repository, not to a language, so
    it is written down rather than guessed.
    """

    def test_no_patterns_keeps_the_built_in_reading(self) -> None:
        assert compile_categories(()) is category_of

    def test_a_pattern_sorts_a_path(self) -> None:
        """The case that the built-in rule misses."""
        sort_into = compile_categories(("test:/[0-9.]*(Tests|Verification)/",), HOLDS)
        chunk = make_chunk("/p/20.Tests/30.Verification/case.yaml", "yaml")
        assert sort_by_language(chunk) == "config"
        assert sort_into(chunk) == "test"

    def test_the_first_match_wins(self) -> None:
        sort_into = compile_categories(("doc:/spec/", "test:/spec/"))
        assert sort_into(make_chunk("/p/spec/a.py")) == "doc"

    def test_an_unmatched_path_falls_back(self) -> None:
        sort_into = compile_categories(("test:/nothing/",), HOLDS)
        assert sort_into(make_chunk("/p/README.md", "markdown")) == "doc"

    def test_the_filter_uses_the_patterns(self) -> None:
        sort_into = compile_categories(("test:Tests/",), HOLDS)
        chunk = make_chunk("/p/20.Tests/case.yaml", "yaml")
        keep = build_result_filter(Filters(type=("test",)), sort_into)
        assert keep is not None and keep(chunk)
        # Without the pattern the same chunk is configuration.
        plain = build_result_filter(Filters(type=("test",)), sort_by_language)
        assert plain is not None and not plain(chunk)

    def test_a_malformed_rule_names_itself(self) -> None:
        with pytest.raises(ValueError, match="type:regex"):
            compile_categories(("justtext",))

    def test_an_unknown_type_is_reported(self) -> None:
        with pytest.raises(ValueError, match="unknown type"):
            compile_categories(("banana:/x/",))

    def test_an_invalid_expression_is_reported(self) -> None:
        with pytest.raises(ValueError, match="invalid regular expression"):
            compile_categories(("test:(unclosed",))

    def test_the_type_name_is_case_insensitive(self) -> None:
        sort_into = compile_categories(("TEST:/x/",))
        assert sort_into(make_chunk("/p/x/a.py")) == "test"
