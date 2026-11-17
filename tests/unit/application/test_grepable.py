"""Test the grepable entries: paths the index leaves out for a model to grep."""

import pytest

from ish.application.grepable import Grepable, listing, parse_grepable


class TestParse:
    def test_an_entry_is_a_path_and_what_it_holds(self) -> None:
        parsed = parse_grepable([r"(?:^|/)gen/tables(?:/|$)=one table per item"])
        assert parsed == (Grepable(r"(?:^|/)gen/tables(?:/|$)", "one table per item"),)

    def test_the_first_equals_sign_ends_the_path(self) -> None:
        """What a path holds may compare values, and a path may not."""
        parsed = parse_grepable(["/gen/=fields where reset = 0"])
        assert parsed == (Grepable("/gen/", "fields where reset = 0"),)

    def test_an_entry_may_name_only_a_path(self) -> None:
        assert parse_grepable(["/gen/"]) == (Grepable("/gen/", ""),)

    def test_spaces_around_each_part_are_dropped(self) -> None:
        assert parse_grepable([" /gen/ = tables "]) == (Grepable("/gen/", "tables"),)

    def test_an_entry_with_no_path_names_the_option(self) -> None:
        with pytest.raises(ValueError, match="'grepable'.*no path"):
            parse_grepable(["=tables"])

    def test_an_invalid_expression_names_the_option(self) -> None:
        with pytest.raises(ValueError, match="'grepable'.*invalid regular"):
            parse_grepable(["(unclosed=tables"])

    def test_no_entries_is_nothing(self) -> None:
        assert parse_grepable([]) == ()


class TestListing:
    def test_one_line_for_each_entry(self) -> None:
        text = listing(
            (Grepable("/gen/", "generated tables"), Grepable("/fixtures/", ""))
        )
        assert text == "- `/gen/` holds generated tables\n- `/fixtures/`"

    def test_no_entries_is_an_empty_listing(self) -> None:
        assert listing(()) == ""
