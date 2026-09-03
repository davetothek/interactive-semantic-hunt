"""Test the ish tools exposed over MCP."""

import threading
import time
from pathlib import Path

import pytest

from ish.interfaces.mcp.server import IshTools, main
from ish.settings import Settings


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "def load_config():\n"
        "    return {}\n"
        "\n"
        "\n"
        "class Client:\n"
        "    def go(self):\n"
        "        pass\n"
    )
    return tmp_path


@pytest.fixture()
def tools(project: Path, tmp_path_factory):
    from dataclasses import replace

    settings = replace(
        Settings(),
        no_cache=True,
        cache_dir=str(tmp_path_factory.mktemp("idx")),
    )
    made = IshTools(settings, project)
    yield made
    made.close()


class StubEmbedder:
    """Score by shared word count, so ranking is predictable."""

    model_name = "stub"

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text: str):
        low = text.lower()
        return [
            float(low.count("config")),
            float(low.count("client")),
            float(len(low)) / 100.0,
        ]


@pytest.fixture()
def stub_backend(monkeypatch):
    """Replace the embedding backend so tests need no daemon."""
    import ish.bootstrap as bootstrap

    monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", lambda model: StubEmbedder())


class TestListChunks:
    """Verify the survey tool."""

    def test_lists_definitions(self, tools: IshTools, project: Path) -> None:
        out = tools.list_chunks({"path": str(project)})
        assert "load_config" in out
        assert "Client.go" in out

    def test_defaults_to_the_server_root(self, tools: IshTools) -> None:
        assert "load_config" in tools.list_chunks({})

    def test_reports_an_empty_tree(self, tools: IshTools, tmp_path: Path) -> None:
        empty = tmp_path / "nothing"
        empty.mkdir()
        assert "No source files" in tools.list_chunks({"path": str(empty)})

    def test_missing_path_is_reported(self, tools: IshTools) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            tools.list_chunks({"path": "/no/such/place"})


class TestSearch:
    """Verify the ranked search tool."""

    def test_requires_a_query(self, tools: IshTools) -> None:
        with pytest.raises(ValueError, match="query"):
            tools.search({})

    def test_blank_query_is_rejected(self, tools: IshTools) -> None:
        with pytest.raises(ValueError, match="query"):
            tools.search({"query": "   "})

    def test_ranks_results(self, tools: IshTools, stub_backend, project: Path) -> None:
        out = tools.search({"query": "config", "path": str(project)})
        assert "load_config" in out.splitlines()[0]

    def test_limit_is_honored(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        out = tools.search({"query": "config", "path": str(project), "limit": 1})
        assert len(out.splitlines()) == 1

    def test_reports_no_results(
        self, tools: IshTools, stub_backend, tmp_path: Path
    ) -> None:
        empty = tmp_path / "bare"
        empty.mkdir()
        assert "No results" in tools.search({"query": "x", "path": str(empty)})


class TestIndexStatus:
    """Verify the status tool."""

    def test_reports_counts_and_backend(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        out = tools.index_status({"path": str(project)})
        assert "chunks   : 3" in out
        assert "3 python" in out
        assert "ollama" in out


class TestReuse:
    """Verify that an index is opened once and kept warm."""

    def test_same_root_reuses_the_use_case(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        tools.search({"query": "config", "path": str(project)})
        tools.search({"query": "client", "path": str(project)})
        assert len(tools._by_root) == 1

    def test_separate_roots_get_separate_indexes(
        self, tools: IshTools, stub_backend, project: Path, tmp_path: Path
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        (other / "b.py").write_text("def other(): pass\n")

        tools.search({"query": "config", "path": str(project)})
        tools.search({"query": "other", "path": str(other)})
        assert len(tools._by_root) == 2

    def test_close_releases_everything(
        self, project: Path, stub_backend, tmp_path_factory
    ) -> None:
        from dataclasses import replace

        settings = replace(Settings(), no_cache=True)
        made = IshTools(settings, project)
        made.search({"query": "config"})
        assert made._by_root
        made.close()
        assert made._by_root == {}


class TestToolDefinitions:
    """Verify what the host is told about the tools."""

    def test_three_tools_are_offered(self, tools: IshTools) -> None:
        assert [t.name for t in tools.tools()] == [
            "search_code",
            "list_chunks",
            "index_status",
        ]

    def test_search_requires_a_query(self, tools: IshTools) -> None:
        search = tools.tools()[0]
        assert search.schema["required"] == ["query"]

    def test_every_tool_describes_itself(self, tools: IshTools) -> None:
        for tool in tools.tools():
            assert len(tool.description) > 40, tool.name
            assert tool.schema["type"] == "object"


class TestEntryPoint:
    """Verify the process wrapper."""

    def test_empty_stdin_exits_cleanly(self, monkeypatch, tmp_path: Path) -> None:
        import io
        import sys

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        assert main([]) == 0

    def test_protocol_goes_to_stdout_only(self, monkeypatch, tmp_path: Path) -> None:
        """Log output on stdout would corrupt the transport."""
        import io
        import json
        import sys

        monkeypatch.chdir(tmp_path)
        out = io.StringIO()
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
            ),
        )
        monkeypatch.setattr(sys, "stdout", out)
        main([])

        for line in out.getvalue().splitlines():
            json.loads(line)  # every line must be a protocol message


class TestVersionReporting:
    """Verify the version handed to the host."""

    def test_reports_the_installed_version(self) -> None:
        from ish.interfaces.mcp.server import _version

        assert _version() != ""

    def test_falls_back_when_not_installed(self, monkeypatch) -> None:
        import importlib.metadata

        from ish.interfaces.mcp.server import _version

        def missing(name):
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, "version", missing)
        assert _version() == "unknown"


class TestInterrupt:
    """Verify a clean stop when the host goes away."""

    def test_keyboard_interrupt_exits_zero(self, monkeypatch, tmp_path: Path) -> None:
        import io
        import sys

        from ish.interfaces.mcp import server as module

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        monkeypatch.setattr(sys, "stdout", io.StringIO())

        def interrupted(self, stream_in, stream_out):
            raise KeyboardInterrupt

        monkeypatch.setattr(module.Server, "serve", interrupted)
        assert module.main([]) == 0


class TestListingRespectsTheResultFilter:
    """Verify the listing tool narrows the same way a search does."""

    def test_language_filter_applies(self, project: Path, tmp_path_factory) -> None:
        from dataclasses import replace

        (project / "guide.md").write_text("# Guide\n\nText.\n")

        settings = replace(
            Settings(),
            no_cache=True,
            cache_dir=str(tmp_path_factory.mktemp("idx")),
            lang=("markdown",),
        )
        tools = IshTools(settings, project)
        try:
            out = tools.list_chunks({"path": str(project)})
        finally:
            tools.close()

        assert "Guide" in out
        assert "load_config" not in out


class TestOnlyQueryScopeIsOverridable:
    """Verify no tool accepts an option that decides what is indexed.

    A call that could set an index-scope option would make the next
    refresh prune whatever that call excluded. Searching one language
    over MCP would delete every other language from the index.
    """

    def _properties(self, tools: IshTools) -> set[str]:
        names: set[str] = set()
        for tool in tools.tools():
            names.update(tool.schema.get("properties", {}))
        return names

    def test_no_index_scope_option_is_exposed(self, tools: IshTools) -> None:
        from ish.settings import option_names, query_scope_names

        index_scope = set(option_names()) - set(query_scope_names())
        offered = self._properties(tools)

        leaked = offered & index_scope
        assert not leaked, f"MCP must not accept index-scope options: {leaked}"

    def test_every_exposed_option_is_a_setting_or_an_input(
        self, tools: IshTools
    ) -> None:
        from ish.settings import query_scope_names

        allowed = set(query_scope_names()) | {"query", "path"}
        assert self._properties(tools) <= allowed

    def test_search_offers_the_narrowing_options(self, tools: IshTools) -> None:
        search = next(t for t in tools.tools() if t.name == "search_code")
        properties = search.schema["properties"]
        assert "lang" in properties
        assert "under" in properties
        assert "limit" in properties


class TestPerCallNarrowing:
    """Verify a single call can narrow without touching the index."""

    def test_lang_narrows_one_call(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        (project / "guide.md").write_text("# Guide\n\nSome configuration text.\n")

        everything = tools.search({"query": "config", "path": str(project)})
        markdown = tools.search(
            {"query": "config", "path": str(project), "lang": ["markdown"]}
        )

        assert "load_config" in everything
        assert "load_config" not in markdown
        assert "Guide" in markdown

    def test_under_narrows_one_call(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        nested = project / "inner"
        nested.mkdir()
        (nested / "deep.py").write_text("def deep_config(): return 1\n")

        narrowed = tools.search(
            {"query": "config", "path": str(project), "under": "/inner/"}
        )
        assert "deep_config" in narrowed
        assert "load_config" not in narrowed

    def test_narrowing_leaves_the_index_whole(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        """The hazard this rule exists to prevent."""
        (project / "guide.md").write_text("# Guide\n\nText.\n")

        tools.search({"query": "config", "path": str(project), "lang": ["markdown"]})
        after = tools.index_status({"path": str(project)})

        assert "python" in after
        assert "markdown" in after

    def test_a_call_without_narrowing_uses_the_configured_value(
        self, project: Path, stub_backend, tmp_path_factory
    ) -> None:
        from dataclasses import replace

        settings = replace(
            Settings(),
            no_cache=True,
            cache_dir=str(tmp_path_factory.mktemp("idx")),
            lang=("markdown",),
        )
        made = IshTools(settings, project)
        try:
            out = made.search({"query": "config", "path": str(project)})
        finally:
            made.close()
        assert "No results" in out or "load_config" not in out


class TestOutputShape:
    """Verify a caller can ask for the shape it can parse."""

    def test_grep_shape_on_request(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        out = tools.search({"query": "config", "path": str(project), "format": "grep"})
        for line in out.splitlines():
            parts = line.split(":")
            assert parts[1].isdigit(), line
            assert parts[2] == "1", line

    def test_plain_shape_by_default(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        out = tools.search({"query": "config", "path": str(project)})
        assert out.startswith("[")

    def test_listing_honours_the_shape(self, tools: IshTools, project: Path) -> None:
        out = tools.list_chunks({"path": str(project), "format": "grep"})
        assert all(line.split(":")[2] == "1" for line in out.splitlines())

    def test_the_shape_is_offered_by_the_tools(self, tools: IshTools) -> None:
        search = next(t for t in tools.tools() if t.name == "search_code")
        assert "format" in search.schema["properties"]


class TestQueryOfOnlyFilters:
    """Verify a query that narrows but asks nothing is reported."""

    def test_filters_alone_are_an_error(self, tools: IshTools, project: Path) -> None:
        with pytest.raises(ValueError, match="only filters"):
            tools.search({"query": "lang:python type:code", "path": str(project)})

    def test_a_query_beside_a_filter_is_fine(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        assert tools.search({"query": "lang:python config", "path": str(project)})


class TestRefreshIsNotPerCall:
    """Verify a question never waits for a walk of the tree.

    An editor asks on every character. Re-checking each time walks the
    tree, and for a parent read from the indexes below it builds every
    chunk only to count them.
    """

    def _counted(self, tools: IshTools, project: Path) -> list[int]:
        counted: list[int] = []
        use_case = tools._search_for(project.resolve())
        original = use_case.build_index

        def watched(root, on_progress=None):
            counted.append(1)
            return original(root, on_progress)

        use_case.build_index = watched
        return counted

    def test_a_burst_of_queries_re_checks_once(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        counted = self._counted(tools, project)
        for _ in range(5):
            tools.search({"query": "config", "path": str(project)})
        assert counted == [1]

    def test_the_query_path_never_walks_the_tree_again(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        """Freshness is a thread's job, so a question never waits for it."""
        counted = self._counted(tools, project)
        for _ in range(20):
            tools.search({"query": "config", "path": str(project)})
        tools.close()
        assert counted == [1]

    def test_the_first_query_always_re_checks(
        self, tools: IshTools, stub_backend, project: Path
    ) -> None:
        counted = self._counted(tools, project)
        tools.search({"query": "config", "path": str(project)})
        assert counted == [1]


class TestAnEditBecomesSearchable:
    """Verify a file changed while the server runs comes into view.

    The server outlives the files it describes, and an editor asks about
    code it is in the middle of changing.
    """

    @pytest.fixture()
    def quick(self, project: Path, stub_backend, monkeypatch) -> IshTools:
        """A server that re-checks often enough to watch."""
        from ish.settings import Settings

        settings = Settings(no_cache=False, git=False, refresh_seconds=0)
        return IshTools(settings, project)

    def _wait_for(self, tools: IshTools, project: Path, symbol: str) -> bool:
        for _ in range(100):
            out = tools.search({"query": "gadget", "path": str(project)})
            if symbol in out:
                return True
            time.sleep(0.05)
        return False

    def test_a_new_file_is_found_without_restarting(
        self, quick: IshTools, project: Path
    ) -> None:
        quick.search({"query": "gadget", "path": str(project)})
        (project / "late.py").write_text("def gadget_maker():\n    return 1\n")
        try:
            assert self._wait_for(quick, project, "gadget_maker")
        finally:
            quick.close()

    def test_the_watch_starts_once_for_a_tree(
        self, quick: IshTools, project: Path
    ) -> None:
        before = threading.active_count()
        for _ in range(4):
            quick.search({"query": "config", "path": str(project)})
        try:
            assert threading.active_count() <= before + 1
        finally:
            quick.close()

    def test_closing_asks_the_watch_to_stop(
        self, quick: IshTools, project: Path
    ) -> None:
        quick.search({"query": "config", "path": str(project)})
        quick.close()
        assert quick._closing.is_set()

    def test_the_watch_thread_is_a_daemon(self, quick: IshTools, project: Path) -> None:
        """A refresh in flight must never hold the server open."""
        quick.search({"query": "config", "path": str(project)})
        try:
            watchers = [
                t for t in threading.enumerate() if t.name.startswith("ish-refresh")
            ]
            assert watchers and all(t.daemon for t in watchers)
        finally:
            quick.close()

    def test_a_failing_refresh_does_not_kill_the_watch(
        self, quick: IshTools, project: Path, monkeypatch, caplog
    ) -> None:
        """A tree that cannot be read must not end the watch silently."""
        from ish import bootstrap

        def explode(*args, **kwargs):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(bootstrap, "refresh_indexes", explode)
        with caplog.at_level("WARNING", logger="ish"):
            quick.search({"query": "config", "path": str(project)})
            time.sleep(0.3)
        quick.close()
        assert "Cannot refresh" in caplog.text
