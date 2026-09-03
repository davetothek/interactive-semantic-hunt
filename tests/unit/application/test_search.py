"""Test the Search orchestration use case against real collaborators."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from ish.adapters.vector_store.pure_python import PurePythonVectorStore
from ish.application.scan import Scan
from ish.application.search import (
    TYPES,
    Filters,
    Search,
    build_result_filter,
    canonical_language,
    category_of,
    compile_categories,
    parse_query,
)
from ish.domain.chunk import Chunk


class WordParser:
    """Emit one chunk per line, so a file's chunks are easy to predict."""

    language = "python"
    suffixes = frozenset({".py"})

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        return [
            Chunk(
                path=path,
                text=line,
                kind="function",
                language="python",
                symbol=line.strip(),
                start_line=n,
                end_line=n,
            )
            for n, line in enumerate(source.splitlines(), 1)
            if line.strip()
        ]


class CountingEmbedder:
    """Return a deterministic vector and record every batch it was given."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    @property
    def texts_embedded(self) -> int:
        return sum(len(batch) for batch in self.batches)

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.batches.append(list(texts))
        return [[float(len(t)), float(t.count("a"))] for t in texts]

    def embed_query(self, text: str) -> Sequence[float]:
        self.batches.append([text])
        return [float(len(text)), float(text.count("a"))]


@pytest.fixture()
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("alpha\nbeta\n")
    return tmp_path


def build(embedder: CountingEmbedder, store=None, **options) -> Search:
    return Search(
        scan=Scan(parsers=[WordParser()]),
        embedder=embedder,
        vector_store=store or PurePythonVectorStore(),
        **options,
    )


class TestSearchUseCase:
    """Verify indexing and querying."""

    def test_indexes_every_chunk(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        search = build(embedder)
        chunks = search.build_index(project)
        assert chunks is not None
        assert {c.symbol for c in chunks} == {"alpha", "beta"}

    def test_empty_tree_returns_none(
        self, embedder: CountingEmbedder, tmp_path: Path
    ) -> None:
        assert build(embedder).build_index(tmp_path) is None
        assert embedder.texts_embedded == 0

    def test_query_returns_ranked_results(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        search = build(embedder)
        search.build_index(project)
        results = search.search("alpha", limit=2)
        assert results
        assert all(isinstance(score, float) for _, score in results)

    def test_run_indexes_then_queries(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        results = build(embedder).run(project, "alpha", limit=1)
        assert len(results) == 1

    def test_run_on_empty_tree(
        self, embedder: CountingEmbedder, tmp_path: Path
    ) -> None:
        assert build(embedder).run(tmp_path, "anything") == []

    def test_query_embed_failure_returns_nothing(self, project: Path) -> None:
        """A backend that returns no vector must not raise."""

        class SilentEmbedder(CountingEmbedder):
            def embed_query(self, text):
                return []

        search = build(SilentEmbedder())
        search.build_index(project)
        assert search.search("q") == []

    def test_close_releases_the_store(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        search = build(embedder)
        search.build_index(project)
        search.close()


class TestIncrementalBehavior:
    """Verify that a second run reuses the first run's work."""

    def test_unchanged_tree_embeds_nothing_again(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        first = embedder.texts_embedded

        build(embedder, store).build_index(project)
        assert embedder.texts_embedded == first

    def test_edited_file_embeds_only_the_new_chunk(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        before = embedder.texts_embedded

        (project / "a.py").write_text("alpha\ngamma\n")
        build(embedder, store).build_index(project)

        # "alpha" is unchanged, so only "gamma" needs a vector.
        assert embedder.texts_embedded == before + 1

    def test_deleted_file_is_dropped(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        (project / "a.py").unlink()

        assert build(embedder, store).build_index(project) is None
        assert store.file_stamps() == {}

    def test_renamed_file_reuses_vectors(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        """Content-keyed vectors survive a move."""
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        before = embedder.texts_embedded

        (project / "a.py").rename(project / "b.py")
        build(embedder, store).build_index(project)

        assert embedder.texts_embedded == before
        assert set(store.file_stamps()) == {project / "b.py"}

    def test_reindex_rebuilds_without_re_embedding(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        store = PurePythonVectorStore()
        build(embedder, store).build_index(project)
        before = embedder.texts_embedded

        forced = build(embedder, store, reindex=True)
        chunks = forced.build_index(project)

        assert chunks is not None
        assert embedder.texts_embedded == before


class TestResultFilters:
    """Verify the query-scope filters.

    These narrow what a search returns. They must never reach the index,
    or the next run would prune everything they exclude.
    """

    @pytest.fixture()
    def mixed(self, tmp_path: Path) -> Path:
        (tmp_path / "src").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "src" / "a.py").write_text("alpha\n")
        (tmp_path / "docs" / "b.py").write_text("beta\n")
        return tmp_path

    def _search(self, embedder, mixed: Path, **kwargs) -> Search:
        return build(
            embedder,
            keep=build_result_filter(
                Filters(
                    lang=tuple(kwargs.pop("lang", ())),
                    under=kwargs.pop("under", ""),
                    type=tuple(kwargs.pop("type", ())),
                )
            ),
            **kwargs,
        )

    def test_no_filter_returns_everything(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed)
        search.build_index(mixed)
        assert len(search.all_chunks()) == 2

    def test_under_narrows_by_path(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, under="/docs/")
        search.build_index(mixed)
        assert [c.symbol for c in search.all_chunks()] == ["beta"]

    def test_lang_narrows_by_language(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, lang=["nothing"])
        search.build_index(mixed)
        assert search.all_chunks() == []

    def test_lang_keeps_a_matching_language(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, lang=["python"])
        search.build_index(mixed)
        assert len(search.all_chunks()) == 2

    def test_search_respects_the_filter(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        search = self._search(embedder, mixed, under="/docs/")
        search.build_index(mixed)
        results = search.search("alpha", limit=5)
        assert all("/docs/" in str(c.path) for c, _ in results)

    def test_the_filter_does_not_shrink_the_index(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        """A narrowed query must leave every file indexed."""
        store = PurePythonVectorStore()
        build(
            embedder, store, keep=build_result_filter(Filters(under="/docs/"))
        ).build_index(mixed)

        assert len(store.file_stamps()) == 2
        assert len(store.chunks()) == 2

    def test_invalid_under_expression_is_reported(
        self, embedder: CountingEmbedder, mixed: Path
    ) -> None:
        with pytest.raises(ValueError, match="'under'"):
            self._search(embedder, mixed, under="(unclosed")


class TestReadOnlyFederation:
    """Verify a search over several indexes does not try to refresh."""

    def test_build_index_returns_stored_chunks(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        from ish.adapters.vector_store.federated import FederatedVectorStore

        indexed = PurePythonVectorStore()
        build(embedder, indexed).build_index(project)
        before = embedder.texts_embedded

        federated = FederatedVectorStore(None, [indexed])
        search = build(embedder, federated)
        chunks = search.build_index(project)

        assert chunks
        # Nothing was parsed or embedded again.
        assert embedder.texts_embedded == before

    def test_an_empty_federation_reports_nothing(
        self, embedder: CountingEmbedder, project: Path
    ) -> None:
        from ish.adapters.vector_store.federated import FederatedVectorStore

        search = build(embedder, FederatedVectorStore(None, []))
        assert search.build_index(project) is None


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


class TestCategories:
    """Verify how a chunk is sorted into code, doc, test, or config."""

    @staticmethod
    def _chunk(path: str, language: str = "python") -> Chunk:
        return Chunk(
            path=Path(path),
            text="x",
            kind="function",
            language=language,
            symbol="x",
            start_line=1,
            end_line=1,
        )

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
        assert category_of(self._chunk(path, language)) == expected

    def test_a_fixture_counts_as_a_test_not_config(self) -> None:
        """A YAML fixture belongs with the tests that read it."""
        assert category_of(self._chunk("/p/tests/data/case.yaml", "yaml")) == "test"

    def test_a_doc_inside_tests_counts_as_a_test(self) -> None:
        assert category_of(self._chunk("/p/tests/README.md", "markdown")) == "test"

    def test_every_category_is_listed(self) -> None:
        assert set(TYPES) == {"code", "doc", "test", "config"}


class TestTypeFilter:
    """Verify the type filter narrows results."""

    @staticmethod
    def _chunks() -> list[Chunk]:
        make = TestCategories._chunk
        return [
            make("/p/src/a.py", "python"),
            make("/p/README.md", "markdown"),
            make("/p/tests/test_a.py", "python"),
            make("/p/deploy.yaml", "yaml"),
        ]

    def test_one_type(self) -> None:
        keep = build_result_filter(Filters(type=("doc",)))
        assert keep is not None
        assert [c.path.name for c in self._chunks() if keep(c)] == ["README.md"]

    def test_several_types(self) -> None:
        keep = build_result_filter(Filters(type=("doc", "test")))
        assert keep is not None
        kept = {c.path.name for c in self._chunks() if keep(c)}
        assert kept == {"README.md", "test_a.py"}

    def test_type_and_language_both_apply(self) -> None:
        keep = build_result_filter(Filters(lang=("python",), type=("code",)))
        assert keep is not None
        assert [c.path.name for c in self._chunks() if keep(c)] == ["a.py"]

    def test_no_type_keeps_everything(self) -> None:
        assert build_result_filter(Filters()) is None


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


class TestConfigurableCategories:
    """Verify a repository can say what its own paths mean.

    A naming convention belongs to a repository, not to a language, so
    it is written down rather than guessed.
    """

    @staticmethod
    def _chunk(path: str, language: str = "python") -> Chunk:
        return Chunk(
            path=Path(path),
            text="x",
            kind="function",
            language=language,
            symbol="x",
            start_line=1,
            end_line=1,
        )

    def test_no_patterns_keeps_the_built_in_reading(self) -> None:
        assert compile_categories(()) is category_of

    def test_a_pattern_sorts_a_path(self) -> None:
        """The case that the built-in rule misses."""
        sort_into = compile_categories(("test:/[0-9.]*(Testing|Verification)/",))
        chunk = self._chunk("/p/20.Tests/30.Verification/case.yaml", "yaml")
        assert category_of(chunk) == "config"
        assert sort_into(chunk) == "test"

    def test_the_first_match_wins(self) -> None:
        sort_into = compile_categories(("doc:/spec/", "test:/spec/"))
        assert sort_into(self._chunk("/p/spec/a.py")) == "doc"

    def test_an_unmatched_path_falls_back(self) -> None:
        sort_into = compile_categories(("test:/nothing/",))
        assert sort_into(self._chunk("/p/README.md", "markdown")) == "doc"

    def test_the_filter_uses_the_patterns(self) -> None:
        sort_into = compile_categories(("test:Testing/",))
        chunk = self._chunk("/p/20.Tests/case.yaml", "yaml")
        keep = build_result_filter(Filters(type=("test",)), sort_into)
        assert keep is not None and keep(chunk)
        # Without the pattern the same chunk is configuration.
        plain = build_result_filter(Filters(type=("test",)))
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
        assert sort_into(self._chunk("/p/x/a.py")) == "test"
