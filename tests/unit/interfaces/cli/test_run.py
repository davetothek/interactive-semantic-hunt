"""Test the entry point of `ish`: the first word names the program to run."""

import subprocess
import sys
from pathlib import Path

import pytest

from ish.interfaces import daemon
from ish.interfaces.cli import complete, run
from ish.interfaces.cli import main as search_program
from ish.interfaces.mcp import server
from ish.interfaces.mcp.transport import TOKEN_VARIABLE


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "foo.py").write_text("def load_config():\n    return {}\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestComplete:
    def test_it_finishes_a_filter_word(self, project: Path, capsys) -> None:
        assert run(["complete", "ty", str(project)]) == 0
        assert capsys.readouterr().out == "type:"

    def test_it_prints_what_ish_complete_prints(self, project: Path, capsys) -> None:
        words = ["--candidates", "type:", str(project)]
        assert run(["complete", *words]) == 0
        through_ish = capsys.readouterr().out
        assert complete.main(words) == 0
        assert through_ish == capsys.readouterr().out
        assert through_ish


class TestServe:
    def test_it_starts_the_server_of_ish_serve(self, project: Path, capsys) -> None:
        assert run(["serve", str(project / "nowhere")]) == 1
        assert "is not a directory" in capsys.readouterr().err

    def test_the_usage_names_the_command_given(self, capsys) -> None:
        with pytest.raises(SystemExit):
            run(["serve", "--help"])
        assert capsys.readouterr().out.startswith("usage: ish serve ")
        with pytest.raises(SystemExit):
            daemon.main(["--help"])
        assert capsys.readouterr().out.startswith("usage: ish-serve ")


class TestMcp:
    def test_it_starts_the_server_of_ish_mcp(
        self, project: Path, monkeypatch, caplog
    ) -> None:
        monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
        assert run(["mcp", "--http", "127.0.0.1:0"]) == 1
        assert TOKEN_VARIABLE in caplog.text

    def test_the_usage_names_the_command_given(self, capsys) -> None:
        with pytest.raises(SystemExit):
            run(["mcp", "--help"])
        assert capsys.readouterr().out.startswith("usage: ish mcp ")
        with pytest.raises(SystemExit):
            server.main(["--help"])
        assert capsys.readouterr().out.startswith("usage: ish-mcp ")


class TestRemote:
    def test_it_starts_the_program_that_reads_a_server(
        self, project: Path, caplog
    ) -> None:
        assert run(["remote", "search", "load_config"]) == 1
        assert "No server is set" in caplog.text

    def test_the_usage_names_the_command_given(self, capsys) -> None:
        with pytest.raises(SystemExit):
            run(["remote", "--help"])
        assert capsys.readouterr().out.startswith("usage: ish remote ")

    def test_the_rest_of_the_line_reaches_it(self, monkeypatch) -> None:
        from ish.interfaces.cli import remote

        seen: list[list[str]] = []
        monkeypatch.setattr(remote, "main", lambda argv: seen.append(argv) or 0)
        assert run(["remote", "ask", "why"]) == 0
        assert seen == [["ask", "why"]]


class TestTheSearchProgram:
    def test_a_line_with_no_command_reaches_it(self, project: Path, capsys) -> None:
        assert run([str(project), "--no-git"]) == 0
        assert "foo.py:1-2  function  load_config" in capsys.readouterr().out

    @pytest.mark.parametrize("word", ["search", "ask", "pick"])
    def test_its_commands_reach_it_whole(self, word: str, monkeypatch) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr(search_program, "main", lambda argv: seen.append(argv) or 0)
        assert run([word, "why"]) == 0
        assert seen == [[word, "why"]]

    def test_two_dashes_make_a_program_word_a_query(self, monkeypatch) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr(search_program, "main", lambda argv: seen.append(argv) or 0)
        assert run(["--", "serve"]) == 0
        assert seen == [["--", "serve"]]

    def test_the_command_line_of_the_process_is_the_default(
        self, project: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["ish", "complete", "ty", str(project)])
        assert run() == 0
        assert capsys.readouterr().out == "type:"


def _loaded(code: str) -> set[str]:
    """Return the ish modules a fresh interpreter holds after *code*.

    Read the last line only, because *code* may print before it.
    """
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys\n{code}\n"
            "print(*sorted(m for m in sys.modules if m.startswith('ish')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(done.stdout.splitlines()[-1].split())


class TestWhatACommandImports:
    """Hold each program to its own imports. Interpreter start is the cost."""

    def test_the_entry_point_imports_no_program(self) -> None:
        assert _loaded("import ish.interfaces.cli") == {
            "ish",
            "ish.interfaces",
            "ish.interfaces.cli",
        }

    def test_ish_complete_imports_what_ish_complete_imports(self) -> None:
        through_ish = _loaded(
            "import io, contextlib\n"
            "from ish.interfaces.cli import run\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    run(['complete', 'ty'])"
        )
        alone = _loaded(
            "import io, contextlib\n"
            "from ish.interfaces.cli.complete import main\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n"
            "    main(['ty'])"
        )
        assert through_ish == alone
        assert "ish.interfaces.cli.main" not in through_ish

    def test_a_search_imports_no_server_and_no_remote_client(self) -> None:
        loaded = _loaded(
            "from ish.interfaces.cli import run\n"
            "try:\n"
            "    run(['search', 'query', '--version'])\n"
            "except SystemExit:\n"
            "    pass"
        )
        assert "ish.interfaces.cli.main" in loaded
        apart = (
            "ish.interfaces.mcp",
            "ish.interfaces.daemon",
            "ish.interfaces.cli.remote",
            "ish.interfaces.python.remote",
            "ish.adapters.chunk_source",
            "ish.adapters.authenticator",
            "ish.application.ports.authenticator",
        )
        assert not [name for name in loaded if name.startswith(apart)]
        assert "ish.interfaces.cli.complete" not in loaded

    def test_a_search_with_a_server_set_imports_no_remote_client(self) -> None:
        loaded = _loaded(
            "import os\n"
            "os.environ['ISH_REMOTE'] = 'http://127.0.0.1:9/mcp'\n"
            "from ish.interfaces.cli import run\n"
            "try:\n"
            "    run(['search', 'query', '--version'])\n"
            "except SystemExit:\n"
            "    pass"
        )
        apart = ("ish.interfaces.cli.remote", "ish.adapters.chunk_source")
        assert not [name for name in loaded if name.startswith(apart)]

    def test_a_local_mcp_server_imports_no_remote_client(self) -> None:
        loaded = _loaded(
            "import io, os, sys\n"
            "os.environ['ISH_REMOTE'] = 'http://127.0.0.1:9/mcp'\n"
            "sys.stdin = io.StringIO('')\n"
            "from ish.interfaces.cli import run\n"
            "run(['mcp'])"
        )
        assert "ish.interfaces.mcp.server" in loaded
        apart = (
            "ish.interfaces.mcp.remote",
            "ish.interfaces.python.remote",
            "ish.adapters.chunk_source",
        )
        assert not [name for name in loaded if name.startswith(apart)]

    def test_ish_remote_imports_no_server_and_no_picker(self) -> None:
        loaded = _loaded(
            "import io, contextlib\n"
            "from ish.interfaces.cli import run\n"
            "with contextlib.redirect_stderr(io.StringIO()):\n"
            "    run(['remote', 'search', 'query'])"
        )
        assert "ish.interfaces.cli.remote" in loaded
        apart = ("ish.interfaces.mcp", "ish.interfaces.daemon", "ish.interfaces.tui")
        assert not [name for name in loaded if name.startswith(apart)]
