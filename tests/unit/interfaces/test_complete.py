"""Test completing a filter word typed into a query."""

from pathlib import Path

import pytest

from ish.interfaces.cli.complete import main
from ish.interfaces.complete import KEYS, candidates, complete
from ish.settings import Settings


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / ".hidden").mkdir()
    return tmp_path


def grow(text: str, project: Path) -> str:
    return complete(text, Settings(), project)


class TestKeys:
    """Verify the filter words themselves complete."""

    def test_a_prefix_of_one_key_finishes_it(self, project: Path) -> None:
        assert grow("ty", project) == "type:"

    def test_a_key_takes_no_trailing_space(self, project: Path) -> None:
        """The value goes straight after the colon."""
        assert not grow("un", project).endswith(" ")

    def test_an_ambiguous_prefix_grows_as_far_as_it_can(self, project: Path) -> None:
        """`lang:` and `under:` share nothing, so nothing grows."""
        assert grow("", project) == ""

    def test_a_word_that_is_no_key_is_left_alone(self, project: Path) -> None:
        assert grow("exposure", project) == "exposure"

    def test_only_the_last_word_is_touched(self, project: Path) -> None:
        assert grow("state machine ty", project) == "state machine type:"

    def test_every_key_is_offered(self) -> None:
        assert set(KEYS) == {"lang:", "type:", "under:"}


class TestTypeValues:
    def test_one_match_finishes_and_spaces(self, project: Path) -> None:
        assert grow("type:d", project) == "type:doc "

    def test_several_matches_grow_to_what_they_share(self, project: Path) -> None:
        assert grow("type:co", project) == "type:co"

    def test_an_exact_value_is_kept(self, project: Path) -> None:
        assert grow("type:test", project) == "type:test "

    def test_an_unknown_value_is_left_alone(self, project: Path) -> None:
        assert grow("type:zebra", project) == "type:zebra"


class TestLanguageValues:
    def test_a_registered_name_finishes(self, project: Path) -> None:
        assert grow("lang:mark", project) == "lang:markdown "

    def test_an_alias_finishes(self, project: Path) -> None:
        assert grow("lang:ad", project) == "lang:adoc "

    def test_shared_openings_grow_together(self, project: Path) -> None:
        """c, c++, cc, cpp and cxx all begin with c, so c is as far as it goes."""
        assert grow("lang:c", project) == "lang:c"

    def test_one_more_character_makes_progress(self, project: Path) -> None:
        assert grow("lang:cp", project) == "lang:cpp "


class TestPathValues:
    def test_a_subtree_is_offered_in_the_written_form(self, project: Path) -> None:
        assert grow("under:/s", project) == "under:/src/"

    def test_a_nested_subtree_is_offered(self, project: Path) -> None:
        assert grow("under:/src/d", project) == "under:/src/deep/ "

    def test_a_hidden_directory_is_not_offered(self, project: Path) -> None:
        assert grow("under:/.h", project) == "under:/.h"

    def test_an_unreadable_directory_is_skipped(self, project: Path) -> None:
        locked = project / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            assert grow("under:/l", project) == "under:/locked/ "
        finally:
            locked.chmod(0o700)


class TestCandidates:
    def test_nothing_is_offered_when_one_answer_fits(self, project: Path) -> None:
        assert candidates("type:d", Settings(), project) == []

    def test_the_choices_are_named_when_several_fit(self, project: Path) -> None:
        assert candidates("type:", Settings(), project) == [
            "code",
            "config",
            "doc",
            "test",
        ]

    def test_keys_are_named_too(self, project: Path) -> None:
        assert candidates("", Settings(), project) == list(KEYS)


class TestTheCommand:
    def test_it_prints_the_completion(self, project: Path, capsys) -> None:
        assert main(["type:d", str(project)]) == 0
        assert capsys.readouterr().out == "type:doc "

    def test_it_prints_the_choices_when_asked(self, project: Path, capsys) -> None:
        assert main(["--candidates", "type:", str(project)]) == 0
        assert "code" in capsys.readouterr().out

    def test_no_query_is_no_output(self, project: Path, capsys) -> None:
        assert main([]) == 0
        assert capsys.readouterr().out == ""

    def test_a_failure_gives_the_query_back(self, project: Path, capsys, monkeypatch):
        """A completion that fails must never eat what was typed."""
        from ish.interfaces.cli import complete as module

        monkeypatch.setattr(
            module, "load_settings", lambda **k: (_ for _ in ()).throw(OSError("no"))
        )
        assert main(["type:d", str(project)]) == 1
        assert capsys.readouterr().out == "type:d"

    def test_a_failure_offers_no_choices(self, project: Path, capsys, monkeypatch):
        from ish.interfaces.cli import complete as module

        monkeypatch.setattr(
            module, "load_settings", lambda **k: (_ for _ in ()).throw(OSError("no"))
        )
        assert main(["--candidates", "type:", str(project)]) == 1
        assert capsys.readouterr().out == ""

    def test_a_long_list_is_cut_short(self, project: Path, capsys) -> None:
        from ish.interfaces.cli import complete as module

        for index in range(module.MOST_SHOWN + 5):
            (project / f"dir{index}").mkdir()
        main(["--candidates", "under:/d", str(project)])
        shown, marker, tail = capsys.readouterr().out.partition("  ... and ")
        assert marker, "a long list must say how much it left out"
        assert len(shown.split("  ")) == module.MOST_SHOWN
        assert tail.endswith("more")

    def test_a_word_with_one_answer_offers_nothing(self, project: Path, capsys) -> None:
        """Nothing to choose between means nothing to show."""
        assert main(["--candidates", "type:doc", str(project)]) == 0
        assert capsys.readouterr().out == ""
