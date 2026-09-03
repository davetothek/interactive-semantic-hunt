"""Integration test — run the CLI against a temporary project."""

from pathlib import Path

import pytest

from ish.interfaces.cli.main import main
from ish.settings import Settings


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Create a temporary project with nested Python files.

    Layout::

        project/
        ├── foo.py          (function + class with method)
        └── nested/
            └── bar.py      (async function)
    """
    foo = tmp_path / "foo.py"
    foo.write_text(
        "def load_config() -> dict[str, str]:\n"
        "    return {}\n"
        "\n"
        "\n"
        "class Client:\n"
        "    def connect(self) -> None:\n"
        '        print("connected")\n'
    )

    nested = tmp_path / "nested"
    nested.mkdir()
    bar = nested / "bar.py"
    bar.write_text("async def fetch(url: str) -> str:\n    return ''\n")

    return tmp_path


class TestCLIOutput:
    """Verify that the CLI produces the expected output format."""

    def test_lists_all_chunks(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(["", str(project)])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "function" in out
        assert "class" in out
        assert "method" in out
        assert "async_function" in out

    def test_shows_symbols(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["", str(project)])
        out = capsys.readouterr().out
        assert "load_config" in out
        assert "Client" in out
        assert "Client.connect" in out
        assert "fetch" in out

    def test_shows_line_ranges(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["", str(project)])
        out = capsys.readouterr().out
        # Each line must have a "path:N-M" pattern.
        for line in out.strip().splitlines():
            assert ":" in line
            path_part = line.split()[0]
            assert "-" in path_part

    def test_recursive_discovery(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Confirm the CLI finds files in nested directories."""
        main(["", str(project)])
        out = capsys.readouterr().out
        assert "bar.py" in out
        assert "foo.py" in out


class TestCLIEdgeCases:
    """Verify error handling and defaults."""

    def test_nonexistent_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = main(["", "/no/such/path"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    def test_default_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Passing no arguments defaults to '.' and does not crash."""
        exit_code = main([""])
        assert exit_code == 0

    def test_single_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        f = tmp_path / "one.py"
        f.write_text("def hello(): pass\n")
        exit_code = main(["", str(f)])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "hello" in out

    def test_syntax_error_is_reported_and_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        f = tmp_path / "bad.py"
        f.write_text("def broken(:\n")
        exit_code = main(["", str(f)])
        assert exit_code == 0
        err = capsys.readouterr().err
        assert "Cannot parse" in err

    def test_embedder_failure_exits_cleanly(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A backend failure yields exit code 1 and no traceback on stdout."""

        def boom() -> None:
            raise ConnectionError("Failed to connect to Ollama")

        monkeypatch.setattr("ish.adapters.embedder.ollama.OllamaEmbedder", boom)
        (tmp_path / "app.py").write_text("pass\n")

        exit_code = main(["find stuff", str(tmp_path), "--embedder", "ollama"])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Failed to connect to Ollama" in captured.err
        assert captured.out == ""

    def test_missing_backend_exits_cleanly(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing optional dependency yields a clean install hint."""

        def boom() -> None:
            raise ModuleNotFoundError("No module named 'sentence_transformers'")

        monkeypatch.setattr(
            "ish.adapters.embedder.sentence_transformer.SentenceTransformerEmbedder",
            boom,
        )
        (tmp_path / "app.py").write_text("pass\n")

        exit_code = main(["find stuff", str(tmp_path), "--embedder", "st"])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert "not installed" in err


def test_interactive_tui(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that the -i flag successfully triggers the TUI app."""
    from unittest.mock import MagicMock

    from ish.domain.chunk import Chunk

    mock_app_class = MagicMock()
    mock_app_instance = MagicMock()
    mock_app_class.return_value = mock_app_instance

    # Simulate user selecting a chunk
    c1 = Chunk(
        kind="function",
        language="python",
        symbol="dummy",
        path=Path("dummy.py"),
        start_line=10,
        end_line=15,
        text="pass",
    )
    mock_app_instance.run.return_value = (c1, 0.99)

    monkeypatch.setattr("ish.interfaces.tui.app.IshApp", mock_app_class)

    # Mock embedders to avoid instantiation
    monkeypatch.setattr("ish.adapters.embedder.llama_cpp.LlamaCppEmbedder", MagicMock())

    exit_code = main(["", str(project), "-i"])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "dummy.py:10" in out


def test_interactive_tui_ollama(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    mock_app_class = MagicMock()
    mock_app_instance = MagicMock()
    mock_app_class.return_value = mock_app_instance
    mock_app_instance.run.return_value = None
    monkeypatch.setattr("ish.interfaces.tui.app.IshApp", mock_app_class)
    monkeypatch.setattr("ish.adapters.embedder.ollama.OllamaEmbedder", MagicMock())
    exit_code = main(["", str(project), "-i", "--embedder", "ollama"])
    assert exit_code == 0


def test_interactive_tui_st(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    mock_app_class = MagicMock()
    mock_app_instance = MagicMock()
    mock_app_class.return_value = mock_app_instance
    mock_app_instance.run.return_value = None
    monkeypatch.setattr("ish.interfaces.tui.app.IshApp", mock_app_class)
    monkeypatch.setattr(
        "ish.adapters.embedder.sentence_transformer.SentenceTransformerEmbedder",
        MagicMock(),
    )
    exit_code = main(["", str(project), "-i", "--embedder", "st"])
    assert exit_code == 0


class TestConfigFile:
    """Verify that ish.toml reaches the running command."""

    def test_project_config_changes_behavior(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A config-file ignore rule prunes the scan."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.py").write_text("def keep(): pass\n")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "gen.py").write_text("def drop(): pass\n")
        (tmp_path / "ish.toml").write_text('ignore = ["build"]\n')

        exit_code = main(["", str(tmp_path)])
        out = capsys.readouterr().out

        assert exit_code == 0
        assert "keep" in out
        assert "drop" not in out

    def test_broken_config_exits_cleanly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ish.toml").write_text("limit = = 3\n")

        exit_code = main(["", str(tmp_path)])
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "Cannot parse" in captured.err
        assert captured.out == ""


class TestGrepFormat:
    """Verify the shape an editor picker consumes, end to end."""

    def test_scan_output_is_grep_shaped(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(["", str(project), "--format", "grep"])
        out = capsys.readouterr().out

        assert exit_code == 0
        for line in out.strip().splitlines():
            parts = line.split(":")
            assert parts[1].isdigit(), line
            assert parts[2] == "1", line

    def test_plain_remains_the_default(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["", str(project)])
        assert "-" in capsys.readouterr().out.split()[0]


class TestRefreshReportsProgress:
    """Verify a long refresh says what it is doing.

    A command that prints nothing for minutes cannot be told from one
    that has stopped.
    """

    def test_a_terminal_sees_a_progress_line(self, monkeypatch, capsys) -> None:
        from ish.interfaces.cli import main as cli

        monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: True, raising=False)
        cli._progress("Refreshing 2 of 8: 10.Specification")
        cli._progress_done()
        assert "Refreshing 2 of 8" in capsys.readouterr().err

    def test_a_pipe_stays_quiet(self, monkeypatch, capsys) -> None:
        """The log already carries it, so do not write it twice."""
        from ish.interfaces.cli import main as cli

        monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: False, raising=False)
        cli._progress("Refreshing 2 of 8: 10.Specification")
        assert capsys.readouterr().err == ""

    def test_the_line_fits_the_terminal(self, monkeypatch, capsys) -> None:
        from ish.interfaces.cli import main as cli

        monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(cli, "_width", lambda: 20)
        cli._progress("x" * 200)
        written = capsys.readouterr().err
        assert len(written.replace("\r", "").replace("\033[2K", "")) < 20

    def test_every_tree_is_announced(self, tmp_path, monkeypatch) -> None:
        from ish import bootstrap

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

        class Fake:
            model_id = "fake"

            def embed_documents(self, texts):
                return [[1.0, 0.0] for _ in texts]

            def embed_query(self, text):
                return [1.0, 0.0]

        monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())
        root = tmp_path / "proj"
        (root / "a").mkdir(parents=True)
        (root / "a" / "one.py").write_text("def one():\n    pass\n")

        said: list[str] = []
        bootstrap.refresh_indexes(Settings(git=False), root, on_progress=said.append)
        assert any("Refreshing 1 of 1" in line for line in said)
