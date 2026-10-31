"""Test the Python API, which wires the same use cases the CLI wires."""

import time
from pathlib import Path

import pytest

from ish import bootstrap
from ish.application.progress import Progress
from ish.interfaces import heartbeat
from ish.interfaces.python.api import Ish
from ish.settings import Settings


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("def parse_config():\n    return 1\n")
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\nHow to configure it.\n")
    (tmp_path / "tests" / "test_app.py").write_text("def test_parse():\n    pass\n")
    return tmp_path


@pytest.fixture()
def offline(monkeypatch) -> None:
    """Replace the embedder so no model or daemon is needed."""
    from ish import bootstrap

    class Fake:
        model_name = "fake"

        def embed_documents(self, texts):
            return [[float(len(t)), 1.0] for t in texts]

        def embed_query(self, text):
            return [float(len(text)), 1.0]

    monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())


def _ish(project: Path) -> Ish:
    return Ish(project, settings=Settings(no_cache=True, git=False))


class TestLifetime:
    def test_path_is_resolved(self, project: Path) -> None:
        assert _ish(project).path == project.resolve()

    def test_context_manager_closes(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert ish._search is not None
        assert ish._search is None

    def test_reopens_after_close(self, project: Path, offline) -> None:
        ish = _ish(project)
        ish.index()
        ish.close()
        assert ish.index() > 0

    def test_overrides_apply(self, project: Path) -> None:
        assert Ish(project, settings=Settings(), limit=42).settings.limit == 42

    def test_repr_names_the_path(self, project: Path) -> None:
        assert str(project.resolve()) in repr(_ish(project))


class TestReading:
    def test_index_reports_a_count(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            assert ish.index() > 0

    def test_search_never_builds_a_language_model(
        self, project: Path, offline, monkeypatch
    ) -> None:
        """A search sends no text anywhere but the embedder."""

        def refuse(settings):
            raise AssertionError("a search built a language model")

        monkeypatch.setattr(bootstrap, "build_language_model", refuse)
        settings = Settings(
            no_cache=True,
            git=False,
            language_model="stub",
            allowed_language_models=("stub",),
        )
        with Ish(project, settings=settings) as ish:
            assert ish.search("configure", limit=3)

    def test_search_returns_ranked_pairs(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            results = ish.search("configure", limit=3)
            assert results
            assert all(isinstance(score, float) for _chunk, score in results)

    def test_chunks_lists_everything(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert len(ish.chunks()) == ish.index()

    def test_a_filter_narrows_the_listing(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert all(c.language == "markdown" for c in ish.chunks(lang=["markdown"]))

    def test_a_type_filter_narrows_the_listing(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            names = {c.path.name for c in ish.chunks(type=["doc"])}
            assert names == {"guide.md"}

    def test_a_filter_in_the_query_wins(self, project: Path, offline) -> None:
        """A word typed into the query beats the argument."""
        with _ish(project) as ish:
            ish.index()
            results = ish.search("type:doc configure", limit=5, type=["code"])
            assert all(c.path.name == "guide.md" for c, _ in results)

    def test_a_language_alias_is_accepted(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            assert ish.chunks(lang=["md"]) == ish.chunks(lang=["markdown"])


class TestRefreshAll:
    def test_visits_the_tree_and_lets_go_of_the_index(
        self, project: Path, offline, monkeypatch
    ) -> None:
        """A refresh may change which indexes a search should read."""
        monkeypatch.setenv("XDG_DATA_HOME", str(project / ".data"))
        ish = Ish(project, settings=Settings(git=False))
        ish.index()
        assert ish._search is not None

        said: list[Progress] = []
        assert ish.refresh_all(said.append) == [project.resolve()]
        assert ish._search is None
        assert said and said[0].tree == project.resolve()


class TestSharedSession:
    """Verify the behavior every interface relies on the session for."""

    def test_the_index_is_brought_up_to_date_once(
        self, project: Path, offline, monkeypatch
    ) -> None:
        """A resident interface must not walk the tree for every question."""
        with _ish(project) as ish:
            counted: list[int] = []
            original = ish._use_case.build_index
            monkeypatch.setattr(
                ish._use_case,
                "build_index",
                lambda path, on_progress=None: counted.append(1) or original(path),
            )
            ish.search("configure")
            ish.search("configure")
            ish.chunks()
            assert counted == [1]
            ish.index()
            assert counted == [1, 1]

    def test_a_query_of_only_filters_is_refused(self, project: Path, offline) -> None:
        with _ish(project) as ish, pytest.raises(ValueError, match="only filters"):
            ish.search("lang:python type:code")

    def test_filters_rank_query_then_argument_then_configuration(
        self, project: Path
    ) -> None:
        ish = Ish(project, settings=Settings(no_cache=True, git=False, type=("test",)))
        assert ish.filters_of().type == ("test",)
        assert ish.filters_of(type=["doc"]).type == ("doc",)
        assert ish.filters_of("type:code x", type=["doc"]).type == ("code",)

    def test_a_filter_word_alone_lists_what_it_allows(
        self, project: Path, offline
    ) -> None:
        with _ish(project) as ish:
            assert {c.language for c in ish.chunks("lang:markdown")} == {"markdown"}

    def test_scan_reads_the_tree_without_a_backend(self, project: Path) -> None:
        """A listing must not need an embedding model."""
        ish = _ish(project)
        names = {c.path.name for c in ish.scan()}
        assert names == {"app.py", "guide.md", "test_app.py"}
        assert ish._search is None

    def test_scan_applies_the_result_filter(self, project: Path) -> None:
        assert {c.path.name for c in _ish(project).scan(type=["doc"])} == {"guide.md"}


class TestStatus:
    def test_status_counts_what_is_indexed(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            ish.index()
            status = ish.status()
            assert status["path"] == project.resolve()
            assert status["chunks"] > 0
            assert status["files"] == 3
            assert set(status["languages"]) == {"python", "markdown"}
            assert set(status["types"]) == {"code", "doc", "test"}

    def test_status_counts_the_files_that_yielded_nothing(
        self, project: Path, offline
    ) -> None:
        """A file of attribute definitions looks indexed and holds nothing."""
        (project / "docs" / "attrs.adoc").write_text(":project: Widget\n")
        with _ish(project) as ish:
            ish.index()
            status = ish.status()
            assert status["files"] == 3
            assert status["empty_files"] == 1

    def test_status_reads_and_does_not_build(self, project: Path, offline) -> None:
        """A status call that indexed first held the caller for the whole run."""
        with _ish(project) as ish:
            status = ish.status()
            assert status["chunks"] == 0
            assert ish._indexed is False

    def test_the_counts_add_up(self, project: Path, offline) -> None:
        """Every chunk falls into exactly one type."""
        with _ish(project) as ish:
            ish.index()
            status = ish.status()
            assert sum(status["types"].values()) == status["chunks"]
            assert sum(status["languages"].values()) == status["chunks"]


class TestCompleting:
    """Verify the session finishes a filter word, so every picker binds one function."""

    def test_a_key_is_finished(self, project: Path) -> None:
        assert _ish(project).complete("state machine ty") == "state machine type:"

    def test_a_subtree_of_the_session_path_is_offered(self, project: Path) -> None:
        assert _ish(project).candidates("under:/") == ["/docs/", "/src/", "/tests/"]

    def test_one_answer_offers_no_candidates(self, project: Path) -> None:
        assert _ish(project).candidates("type:d") == []


class TestReadingWhatIsStored:
    """Verify a stored read never brings the index up to date.

    A picker lists and searches what the index held before its refresh
    runs, so the read must cost no scan and no embedding.
    """

    def test_a_stored_listing_of_a_new_tree_is_empty(self, project: Path) -> None:
        ish = _ish(project)
        try:
            assert ish.chunks(stored=True) == []
            assert ish._indexed is False
        finally:
            ish.close()

    def test_a_stored_search_does_not_index(self, project: Path, offline) -> None:
        ish = _ish(project)
        try:
            assert ish.search("parse", stored=True) == []
            assert ish._indexed is False
        finally:
            ish.close()

    def test_a_stored_read_sees_what_an_earlier_refresh_wrote(
        self, project: Path, offline
    ) -> None:
        ish = _ish(project)
        try:
            ish.index()
            held = ish.chunks(stored=True)
            assert held == ish.chunks()
            assert ish.search("parse", stored=True) == ish.search("parse")
        finally:
            ish.close()


class TestWarmUp:
    def test_warm_asks_the_backend_and_indexes_nothing(
        self, project: Path, offline, monkeypatch
    ) -> None:
        from ish.application.search import Search

        asked: list[str] = []
        monkeypatch.setattr(Search, "warm", lambda self: asked.append("warm"))
        ish = _ish(project)
        try:
            ish.warm()
            assert asked == ["warm"]
            assert ish._indexed is False
        finally:
            ish.close()


class TestKeptByAServer:
    """Verify the session leaves the refresh to ish-serve when one keeps the tree."""

    @staticmethod
    def _keep(project: Path, **more) -> heartbeat.Status:
        fields: dict = {"tree": project.resolve(), "pid": 4242, "updated": time.time()}
        fields.update(more)
        status = heartbeat.Status(**fields)
        heartbeat.write(heartbeat.status_for(project), status)
        return status

    def test_index_reads_what_the_server_wrote(
        self, project: Path, offline, monkeypatch, caplog
    ) -> None:
        from ish.application.search import Search

        self._keep(project)
        walked: list[Path] = []
        monkeypatch.setattr(
            Search,
            "build_index",
            lambda self, root, on_progress=None: walked.append(root) or 0,
        )
        with _ish(project) as ish, caplog.at_level("INFO", logger="ish"):
            assert ish.index() == 0
            assert ish._indexed is True
            assert ish.kept_by() is not None
            ish.index()
        assert walked == []
        assert caplog.text.count("ish-serve keeps") == 1

    def test_a_stale_server_is_read_past(self, project: Path, offline) -> None:
        self._keep(project, updated=0.0)
        with _ish(project) as ish:
            assert ish.kept_by() is None
            assert ish.index() > 0

    def test_the_status_names_what_the_server_does(
        self, project: Path, offline
    ) -> None:
        self._keep(project, refreshing="Embedded 8 of 16 chunks")
        kept = _ish(project).kept_by()
        assert kept is not None
        assert kept.refreshing == "Embedded 8 of 16 chunks"
        assert kept.pid == 4242

    def test_held_counts_without_refreshing(self, project: Path, offline) -> None:
        with _ish(project) as ish:
            assert ish.held() == 0
            assert ish._indexed is False
            ish.index()
            assert ish.held() == ish.index()

    def test_git_is_asked_once(self, project: Path, offline, monkeypatch) -> None:
        """A process start per question would put git in front of every keystroke."""
        asked: list[Path] = []
        monkeypatch.setattr(
            bootstrap,
            "repository_identity",
            lambda root: asked.append(root) or (None, None),
        )
        with _ish(project) as ish:
            ish.index()
            ish.search("configure")
            ish.kept_by()
            ish.status()
            ish.scan()
        assert asked == [project.resolve()]

    def test_this_process_is_not_a_server(self, project: Path, offline) -> None:
        assert _ish(project).kept_by() is None


class TestAsk:
    """Ish.ask searches the way Ish.search does, then lets a model answer."""

    class Model:
        model_name = "fake-model"

        def __init__(self) -> None:
            self.sources: list = []

        def answer(self, question, sources):
            from ish.application.ports.language_model import Passage, Reply

            self.sources = list(sources)
            return Reply(passages=(Passage("Yes.", ("1",)),), covered=True)

    def test_the_model_is_built_once_on_the_first_question(
        self, project: Path, offline, monkeypatch
    ) -> None:
        built: list[object] = []
        model = self.Model()

        def build(settings):
            built.append(settings)
            return model

        monkeypatch.setattr(bootstrap, "build_language_model", build)
        with _ish(project) as ish:
            assert built == []
            first = ish.ask("how to configure")
            ish.ask("again")
        assert len(built) == 1
        assert first.covered and first.model == "fake-model"
        assert first.parts[0].cites == (first.sources[0],)

    def test_filters_narrow_what_the_model_reads(
        self, project: Path, offline, monkeypatch
    ) -> None:
        model = self.Model()
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s: model)
        with _ish(project) as ish:
            ish.ask("configure", type=["doc"])
        assert model.sources
        assert all("guide.md" in s.title for s in model.sources)

    def test_a_filter_typed_into_the_question_applies(
        self, project: Path, offline, monkeypatch
    ) -> None:
        model = self.Model()
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s: model)
        with _ish(project) as ish:
            ish.ask("lang:python configure")
        assert model.sources
        assert all(".py:" in s.title for s in model.sources)

    def test_at_most_ask_limit_chunks_are_read(
        self, project: Path, offline, monkeypatch
    ) -> None:
        from ish.interfaces.python import api

        for n in range(api.ASK_LIMIT + 3):
            (project / "src" / f"m{n}.py").write_text(f"def f{n}():\n    pass\n")
        model = self.Model()
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s: model)
        with _ish(project) as ish:
            ish.ask("function")
        assert len(model.sources) == api.ASK_LIMIT


class TestLink:
    def test_a_link_uses_the_base_and_this_tree(self, project: Path) -> None:
        from ish.domain.chunk import Chunk

        settings = Settings(
            no_cache=True, git=False, link_base="https://docs.example.com"
        )
        chunk = Chunk(
            project.resolve() / "docs" / "guide.md",
            "t",
            "document",
            "markdown",
            "Guide",
            1,
            3,
        )
        with Ish(project, settings=settings) as ish:
            assert ish.link(chunk) == "https://docs.example.com/docs/guide.md#L1-3"
            assert ish.link(chunk) == ish.link(chunk)

    def test_without_a_base_a_link_is_a_file_uri(self, project: Path) -> None:
        from ish.domain.chunk import Chunk

        chunk = Chunk(
            project.resolve() / "a.md", "t", "document", "markdown", None, 2, 4
        )
        with _ish(project) as ish:
            assert ish.link(chunk).startswith("file://")
            assert ish.link(chunk).endswith("/a.md#L2-4")


class TestAskWithRerank:
    def test_a_rerank_model_widens_the_search_and_picks_ten(
        self, project: Path, offline, monkeypatch
    ) -> None:
        from ish.application.ports.language_model import Passage, Reply, Selection
        from ish.interfaces.python import api

        for n in range(api.ASK_LIMIT + 5):
            (project / "src" / f"m{n}.py").write_text(f"def f{n}():\n    pass\n")
        shown: list[int] = []

        class Model:
            model_name = "m"

            def answer(self, question, sources):
                return Reply(passages=(Passage("ok", ("1",)),), covered=True)

            def select(self, question, candidates, limit):
                shown.append(len(candidates))
                return Selection(ids=tuple(c.id for c in candidates)[:limit])

        monkeypatch.setattr(bootstrap, "build_language_model", lambda s: Model())
        monkeypatch.setattr(bootstrap, "build_reranker", lambda s: Model())
        settings = Settings(no_cache=True, git=False, rerank_candidates=14)
        with Ish(project, settings=settings) as ish:
            answer = ish.ask("function")
        assert shown == [14]
        assert len(answer.sources) == api.ASK_LIMIT


class TestAskTurns:
    def test_ask_turns_reach_the_use_case(
        self, project: Path, offline, monkeypatch
    ) -> None:
        from ish.application.ports.language_model import Passage, Reply

        class Agent:
            model_name = "agent"
            turns_seen: list[int] = []

            def step(self, question, sources, searched, turns_left):
                self.turns_seen.append(turns_left)
                return Reply(passages=(Passage("ok", ("1",)),), covered=True)

        agent = Agent()
        monkeypatch.setattr(bootstrap, "build_language_model", lambda s: agent)
        settings = Settings(no_cache=True, git=False, ask_turns=2)
        with Ish(project, settings=settings) as ish:
            assert ish.ask("configure").turns == 1
        assert agent.turns_seen == [2]


class TestKindOf:
    def test_a_chunk_is_sorted_the_way_the_type_filter_sorts_it(
        self, project: Path
    ) -> None:
        from ish.domain.chunk import Chunk

        def chunk(rel: str, language: str) -> Chunk:
            return Chunk(project.resolve() / rel, "t", "k", language, None, 1, 2)

        with _ish(project) as ish:
            assert ish.kind_of(chunk("docs/guide.md", "markdown")) == "doc"
            assert ish.kind_of(chunk("tests/test_app.py", "python")) == "test"
            assert ish.kind_of(chunk("src/app.py", "python")) == "code"
