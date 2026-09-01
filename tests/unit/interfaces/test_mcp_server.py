"""Test the ish tools exposed over MCP."""

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
