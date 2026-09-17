"""Test the names a reader may type for a language."""

from pathlib import Path

import pytest

from ish.application.filters import Filters, build_result_filter, parse_query
from ish.application.languages import canonical_language
from ish.domain.chunk import Chunk


class TestLanguageAliases:
    """Verify the names a reader may type for a language."""

    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            ("c", "cpp"),
            ("C", "cpp"),
            ("c++", "cpp"),
            ("cxx", "cpp"),
            ("h", "cpp"),
            ("hpp", "cpp"),
            ("adoc", "asciidoc"),
            ("asc", "asciidoc"),
            ("md", "markdown"),
            ("py", "python"),
            ("yml", "yaml"),
        ],
    )
    def test_alias_resolves(self, typed: str, stored: str) -> None:
        assert canonical_language(typed) == stored

    def test_a_canonical_name_is_left_alone(self) -> None:
        for name in ("cpp", "asciidoc", "markdown", "python", "yaml", "json"):
            assert canonical_language(name) == name

    def test_an_unknown_name_is_left_alone(self) -> None:
        """A filter for a language no parser reads returns nothing."""
        assert canonical_language("rust") == "rust"

    def test_filters_store_the_canonical_name(self) -> None:
        assert Filters(lang=("c", "adoc")).lang == ("cpp", "asciidoc")

    def test_two_spellings_collapse_to_one(self) -> None:
        assert Filters(lang=("c", "c++", "cpp")).lang == ("cpp",)

    def test_the_query_line_takes_an_alias(self) -> None:
        assert parse_query("lang:c state machine")[1].lang == ("cpp",)

    def test_a_type_is_lowercased(self) -> None:
        assert Filters(type=("DOC", "Test")).type == ("doc", "test")

    def test_an_alias_filters_the_same_as_the_stored_name(self) -> None:
        chunk = Chunk(
            path=Path("/p/src/a.c"),
            text="x",
            kind="function",
            language="cpp",
            symbol="x",
            start_line=1,
            end_line=1,
        )
        for name in ("c", "c++", "cpp", "H"):
            keep = build_result_filter(Filters(lang=(name,)))
            assert keep is not None
            assert keep(chunk), name
