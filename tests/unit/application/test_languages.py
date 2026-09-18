"""Test resolving the name a reader types to the name a parser is registered under."""

from pathlib import Path

import pytest

from ish.application.filters import Filters, build_result_filter, parse_query
from ish.application.languages import as_typed, resolve_with, unique
from ish.domain.chunk import Chunk

# What a registry of two languages offers, as bootstrap would read it.
NAMES = {
    "cpp": "cpp",
    "c": "cpp",
    "h": "cpp",
    "asciidoc": "asciidoc",
    "adoc": "asciidoc",
}


def chunk(language: str, path: str = "/p/src/a.c") -> Chunk:
    return Chunk(
        path=Path(path),
        text="x",
        kind="function",
        language=language,
        symbol="x",
        start_line=1,
        end_line=1,
    )


class TestResolver:
    """Verify a resolver built over the names a registry offers."""

    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            ("c", "cpp"),
            ("C", "cpp"),
            ("h", "cpp"),
            ("adoc", "asciidoc"),
            (" c ", "cpp"),
        ],
    )
    def test_an_alias_resolves(self, typed: str, stored: str) -> None:
        assert resolve_with(NAMES)(typed) == stored

    def test_a_registered_name_is_left_alone(self) -> None:
        resolve = resolve_with(NAMES)
        for name in ("cpp", "asciidoc"):
            assert resolve(name) == name

    def test_an_unknown_name_is_left_alone(self) -> None:
        """A filter for a language no parser reads returns nothing, not an error."""
        assert resolve_with(NAMES)("rust") == "rust"

    def test_an_empty_registry_resolves_nothing(self) -> None:
        assert resolve_with({})("c") == "c"


class TestAsTyped:
    """Verify the reading used where no registry is to hand."""

    def test_a_name_is_tidied(self) -> None:
        assert as_typed("  CPP ") == "cpp"


class TestUnique:
    def test_repeats_go_and_order_stays(self) -> None:
        assert unique(["b", "a", "b"]) == ("b", "a")


class TestFiltersKeepWhatWasTyped:
    """Verify the filter holds the spelling, and the predicate resolves it."""

    def test_the_spelling_is_kept_for_display(self) -> None:
        """The header should show what the reader typed."""
        assert Filters(lang=("c", "adoc")).lang == ("c", "adoc")
        assert "lang: c" in Filters(lang=("c",)).describe()

    def test_a_name_is_tidied_and_kept_once(self) -> None:
        assert Filters(lang=("C", "c")).lang == ("c",)

    def test_a_type_is_lowercased(self) -> None:
        assert Filters(type=("DOC", "Test")).type == ("doc", "test")

    def test_the_query_line_keeps_the_alias(self) -> None:
        assert parse_query("lang:c state machine")[1].lang == ("c",)

    @pytest.mark.parametrize("typed", ["c", "C", "h", "cpp"])
    def test_an_alias_filters_the_same_as_the_stored_name(self, typed: str) -> None:
        keep = build_result_filter(Filters(lang=(typed,)), resolve=resolve_with(NAMES))
        assert keep is not None
        assert keep(chunk("cpp")), typed

    def test_without_a_resolver_a_spelling_stands_for_itself(self) -> None:
        """All a caller with no registry can know."""
        keep = build_result_filter(Filters(lang=("c",)))
        assert keep is not None
        assert not keep(chunk("cpp"))
        assert keep(chunk("c"))

    def test_two_spellings_of_one_language_match_it_once(self) -> None:
        keep = build_result_filter(
            Filters(lang=("c", "cpp")), resolve=resolve_with(NAMES)
        )
        assert keep is not None
        assert keep(chunk("cpp"))
