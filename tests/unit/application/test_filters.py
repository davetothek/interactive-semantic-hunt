"""Test the query-scope filters and the words a query carries them in."""

from pathlib import Path

from ish.application.categories import by_language
from ish.application.filters import Filters, build_result_filter, parse_query
from ish.domain.chunk import Chunk

# What the registered languages hold, as bootstrap reads it off them.
HOLDS = by_language({"markdown": "doc", "yaml": "config"})


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


class TestParseQuery:
    """Verify filters written into the query text."""

    def test_plain_query_is_untouched(self) -> None:
        assert parse_query("state machine") == ("state machine", Filters())

    def test_language_is_taken_out(self) -> None:
        assert parse_query("lang:cpp state machine") == (
            "state machine",
            Filters(lang=("cpp",)),
        )

    def test_several_languages(self) -> None:
        text, filters = parse_query("a lang:cpp lang:yaml b")
        assert filters.lang == ("cpp", "yaml")
        # The gaps the removed words left must not survive.
        assert text == "a b"

    def test_comma_separated_languages(self) -> None:
        assert parse_query("lang:cpp,yaml x")[1].lang == ("cpp", "yaml")

    def test_path_expression(self) -> None:
        assert parse_query("under:/src/ x") == ("x", Filters(under="/src/"))

    def test_type_is_taken_out(self) -> None:
        assert parse_query("type:doc install") == ("install", Filters(type=("doc",)))

    def test_comma_separated_types(self) -> None:
        assert parse_query("type:doc,test x")[1].type == ("doc", "test")

    def test_all_three_together(self) -> None:
        text, filters = parse_query("lang:cpp type:test under:/src/ errors")
        assert text == "errors"
        assert filters == Filters(lang=("cpp",), under="/src/", type=("test",))

    def test_a_dangling_key_is_left_alone(self) -> None:
        """`lang:` with nothing after it is ordinary text."""
        assert parse_query("lang: dangling")[0] == "lang: dangling"

    def test_a_colon_inside_a_word_is_not_a_filter(self) -> None:
        assert parse_query("slang:cpp") == ("slang:cpp", Filters())

    def test_only_a_filter_leaves_no_query(self) -> None:
        assert parse_query("lang:cpp")[0] == ""


class TestDescribeFilters:
    """Verify what the interface shows the user."""

    def test_nothing_active(self) -> None:
        assert Filters().describe() == ""

    def test_language_only(self) -> None:
        assert Filters(lang=("cpp",)).describe() == "lang: cpp"

    def test_every_filter(self) -> None:
        described = Filters(("cpp", "yaml"), "/src/", ("doc",)).describe()
        assert "cpp, yaml" in described
        assert "/src/" in described
        assert "doc" in described

    def test_empty_filters_are_falsy(self) -> None:
        assert not Filters()
        assert Filters(type=("doc",))


class TestOrElse:
    """Verify that a typed filter overrides the configured one."""

    def test_empty_falls_back(self) -> None:
        base = Filters(lang=("python",), under="/src/", type=("code",))
        assert Filters().or_else(base) == base

    def test_each_field_wins_on_its_own(self) -> None:
        base = Filters(lang=("python",), under="/src/")
        merged = Filters(lang=("cpp",)).or_else(base)
        assert merged.lang == ("cpp",)
        # A field the query did not mention keeps the configured value.
        assert merged.under == "/src/"


class TestTypeFilter:
    """Verify the type filter narrows results."""

    @staticmethod
    def _chunks() -> list[Chunk]:
        return [
            make_chunk("/p/src/a.py", "python"),
            make_chunk("/p/README.md", "markdown"),
            make_chunk("/p/tests/test_a.py", "python"),
            make_chunk("/p/deploy.yaml", "yaml"),
        ]

    def test_one_type(self) -> None:
        keep = build_result_filter(Filters(type=("doc",)), HOLDS)
        assert keep is not None
        assert [c.path.name for c in self._chunks() if keep(c)] == ["README.md"]

    def test_several_types(self) -> None:
        keep = build_result_filter(Filters(type=("doc", "test")), HOLDS)
        assert keep is not None
        kept = {c.path.name for c in self._chunks() if keep(c)}
        assert kept == {"README.md", "test_a.py"}

    def test_type_and_language_both_apply(self) -> None:
        keep = build_result_filter(Filters(lang=("python",), type=("code",)))
        assert keep is not None
        assert [c.path.name for c in self._chunks() if keep(c)] == ["a.py"]

    def test_no_type_keeps_everything(self) -> None:
        assert build_result_filter(Filters()) is None
