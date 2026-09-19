"""Test the registry of languages, which is the parser plug-in point.

Every fact about a language sits on the line that registers it, so
these guards read that line rather than a table elsewhere.
"""

from ish.adapters.parser import (
    CODE,
    PARSERS,
    Language,
    categories,
    spellings,
)
from ish.application.categories import TYPES
from ish.application.ports.parser import Parser


class TestEveryEntry:
    """Guard the registry itself, so a new entry cannot be malformed."""

    def test_each_entry_builds_a_parser_that_satisfies_the_port(self) -> None:
        for name, language in PARSERS.items():
            parser = language.build()
            assert isinstance(parser, Parser), name
            assert parser.language == name, name
            assert parser.suffixes, name
            assert all(s.startswith(".") for s in parser.suffixes), name

    def test_entries_claim_distinct_suffixes(self) -> None:
        """Two parsers claiming one suffix is a hard error at scan time."""
        seen: dict[str, str] = {}
        for name, language in PARSERS.items():
            for suffix in language.build().suffixes:
                assert suffix not in seen, f"{name} and {seen[suffix]} share {suffix}"
                seen[suffix] = name

    def test_each_entry_holds_one_of_the_four_types(self) -> None:
        for name, language in PARSERS.items():
            assert language.category in TYPES, name

    def test_a_language_holds_code_unless_it_says_otherwise(self) -> None:
        assert Language(build=lambda: PARSERS["python"].build()).category == CODE

    def test_an_alias_names_no_other_language(self) -> None:
        """An alias must not shadow a language, or another language's alias."""
        claimed: dict[str, str] = {}
        for name, language in PARSERS.items():
            for alias in language.aliases:
                assert alias not in PARSERS, f"{name} claims the language {alias}"
                assert alias not in claimed, (
                    f"{name} and {claimed[alias]} claim {alias}"
                )
                claimed[alias] = name


class TestSpellings:
    """Verify the names a reader may type come from the entries."""

    def test_a_language_answers_to_its_own_name(self) -> None:
        names = spellings(PARSERS)
        for name in PARSERS:
            assert names[name] == name

    def test_a_language_answers_to_each_alias(self) -> None:
        names = spellings(PARSERS)
        for name, language in PARSERS.items():
            for alias in language.aliases:
                assert names[alias] == name, alias

    def test_one_parser_owns_c_and_cpp(self) -> None:
        """The C++ grammar reads nearly all C, so every spelling names it."""
        names = spellings(PARSERS)
        for spelling in ("c", "c++", "cc", "cxx", "h", "hpp", "cpp"):
            assert names[spelling] == "cpp", spelling

    def test_nothing_else_is_offered(self) -> None:
        """A name no entry declares is not a name a reader may type."""
        assert "rust" not in spellings(PARSERS)


class TestCategories:
    """Verify what each language holds comes from the entries."""

    def test_every_language_is_mapped(self) -> None:
        assert set(categories(PARSERS)) == set(PARSERS)

    def test_prose_and_configuration_are_named(self) -> None:
        holds = categories(PARSERS)
        assert holds["markdown"] == "doc"
        assert holds["asciidoc"] == "doc"
        assert holds["yaml"] == "config"
        assert holds["json"] == "config"

    def test_a_programming_language_holds_code(self) -> None:
        holds = categories(PARSERS)
        assert holds["python"] == "code"
        assert holds["cpp"] == "code"
