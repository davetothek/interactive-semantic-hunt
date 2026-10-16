"""Test the CLI as a client of a resident server.

A plain query goes to the server that listens for the tree, when one
does, so it costs a round trip rather than an index load. A run that
cannot be answered that way, or finds no server, does its own work.
"""

import socket
import threading
from pathlib import Path

import pytest

from ish.interfaces.cli import main as cli
from ish.interfaces.mcp.protocol import Server, Tool
from ish.interfaces.mcp.transport import SocketServer, socket_for

RESULT = "[0.50] a.py:1-2  function  f"


class Recording:
    """A server whose search_code and refresh_index record what they get."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.answer: str | None = RESULT

    def _tool(self, name: str) -> Tool:
        def handler(arguments):
            self.calls.append((name, dict(arguments)))
            if name != "search_code":
                return "Refreshing."
            if self.answer is None:
                raise ValueError("Path does not exist: /x")
            return self.answer

        return Tool(
            name=name, description=name, schema={"type": "object"}, handler=handler
        )

    def server(self) -> Server:
        return Server(
            "ish", "1", [self._tool("search_code"), self._tool("refresh_index")]
        )


class Boom:
    """Stand in for Ish in a run that must never open an index."""

    def __init__(self, *args, **kwargs) -> None:
        raise AssertionError("the run should have asked the server")


class FakeIsh:
    """Stand in for Ish in a run that does its own work."""

    made: list[Path] = []

    def __init__(self, path: Path, *, settings=None) -> None:
        FakeIsh.made.append(path)

    def __enter__(self) -> "FakeIsh":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def search(self, query: str):
        return []

    def scan(self):
        return []


@pytest.fixture()
def project(tmp_path_factory, monkeypatch) -> Path:
    run = tmp_path_factory.mktemp("run")
    project = tmp_path_factory.mktemp("proj")
    (project / "sub").mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(run / "cfg"))
    FakeIsh.made = []
    return project


@pytest.fixture()
def served(project: Path):
    recording = Recording()
    socket_server = SocketServer(recording.server(), socket_for(project))
    thread = threading.Thread(target=socket_server.serve_forever, daemon=True)
    thread.start()
    assert socket_server.ready.wait(5)
    yield recording
    socket_server.shutdown()
    thread.join(5)


class TestAServerAnswers:
    """Verify a plain query is answered over the socket."""

    def test_a_query_goes_to_the_server(
        self, project: Path, served: Recording, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(cli, "Ish", Boom)
        monkeypatch.chdir(project)
        assert cli.main(["parse the config", str(project)]) == 0
        assert capsys.readouterr().out == f"{RESULT}\n"

        assert [name for name, _ in served.calls] == ["search_code", "refresh_index"]
        _name, arguments = served.calls[0]
        assert arguments["query"] == "parse the config"
        assert arguments["path"] == str(project)
        assert arguments["limit"] == 5
        assert arguments["format"] == "plain"
        assert "lang" not in arguments
        assert served.calls[1][1] == {"path": str(project)}

    def test_a_result_is_shown_from_the_working_directory(
        self, project: Path, served: Recording, monkeypatch, capsys
    ) -> None:
        """The server writes a path from the directory asked about.

        The command line shows it from where the command ran, as a run
        of its own would.
        """
        monkeypatch.setattr(cli, "Ish", Boom)
        monkeypatch.chdir(project)
        assert cli.main(["q", str(project / "sub")]) == 0
        assert capsys.readouterr().out == "[0.50] sub/a.py:1-2  function  f\n"

    def test_a_grep_line_is_shown_from_the_working_directory(
        self, project: Path, served: Recording, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(cli, "Ish", Boom)
        monkeypatch.chdir(project)
        served.answer = "a.py:1:1:[0.50] function  f"
        assert cli.main(["--format", "grep", "q", str(project / "sub")]) == 0
        assert capsys.readouterr().out == "sub/a.py:1:1:[0.50] function  f\n"

    def test_an_absolute_path_and_prose_pass_through(
        self, project: Path, served: Recording, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(cli, "Ish", Boom)
        monkeypatch.chdir(project)
        served.answer = "/elsewhere/a.py:1-2  function  f\nnot a result line"
        assert cli.main(["q", str(project / "sub")]) == 0
        out = capsys.readouterr().out
        assert out == "/elsewhere/a.py:1-2  function  f\nnot a result line\n"

    def test_a_subdirectory_reaches_the_server_of_its_tree(
        self, project: Path, served: Recording, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(cli, "Ish", Boom)
        assert cli.main(["q", str(project / "sub")]) == 0
        assert served.calls[0][1]["path"] == str(project / "sub")

    def test_the_narrowing_flags_travel(
        self, project: Path, served: Recording, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(cli, "Ish", Boom)
        argv = [
            "--limit", "3",
            "--lang", "cpp",
            "--type", "doc",
            "--under", "src/",
            "--format", "grep",
            "q",
            str(project),
        ]  # fmt: skip
        assert cli.main(argv) == 0
        _name, arguments = served.calls[0]
        assert arguments["limit"] == 3
        assert arguments["lang"] == ["cpp"]
        assert arguments["type"] == ["doc"]
        assert arguments["under"] == "src/"
        assert arguments["format"] == "grep"

    def test_no_results_prints_nothing(
        self, project: Path, served: Recording, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(cli, "Ish", Boom)
        served.answer = "No results for 'q' under /x."
        assert cli.main(["q", str(project)]) == 0
        assert capsys.readouterr().out == ""

    def test_a_tool_failure_is_reported(
        self, project: Path, served: Recording, monkeypatch, capsys, caplog
    ) -> None:
        monkeypatch.setattr(cli, "Ish", Boom)
        served.answer = None
        assert cli.main(["q", str(project)]) == 1
        assert "Path does not exist" in caplog.text
        assert capsys.readouterr().out == ""


class TestTheRunDoesItsOwnWork:
    """Verify when a run opens the index itself."""

    def test_without_a_server(self, project: Path, monkeypatch) -> None:
        monkeypatch.setattr(cli, "Ish", FakeIsh)
        assert cli.main(["q", str(project)]) == 0
        assert FakeIsh.made == [project]

    def test_with_a_refresh(
        self, project: Path, served: Recording, monkeypatch
    ) -> None:
        monkeypatch.setattr(cli, "Ish", FakeIsh)
        monkeypatch.setattr(cli.bootstrap, "refresh_indexes", lambda *a, **k: [])
        assert cli.main(["--refresh", "q", str(project)]) == 0
        assert FakeIsh.made == [project]
        assert served.calls == []

    def test_with_an_index_scope_flag(
        self, project: Path, served: Recording, monkeypatch
    ) -> None:
        monkeypatch.setattr(cli, "Ish", FakeIsh)
        assert cli.main(["--no-git", "q", str(project)]) == 0
        assert FakeIsh.made == [project]
        assert served.calls == []

    def test_with_a_named_config_file(
        self, project: Path, served: Recording, monkeypatch
    ) -> None:
        config = project / "my.toml"
        config.write_text("limit = 2\n")
        monkeypatch.setattr(cli, "Ish", FakeIsh)
        assert cli.main(["-c", str(config), "q", str(project)]) == 0
        assert FakeIsh.made == [project]
        assert served.calls == []

    def test_a_scan_never_asks(
        self, project: Path, served: Recording, monkeypatch
    ) -> None:
        monkeypatch.setattr(cli, "Ish", FakeIsh)
        assert cli.main([str(project)]) == 0
        assert FakeIsh.made == [project]
        assert served.calls == []

    def test_a_stale_socket_falls_back_and_is_removed(
        self, project: Path, monkeypatch
    ) -> None:
        path = socket_for(project)
        path.parent.mkdir(parents=True)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()

        monkeypatch.setattr(cli, "Ish", FakeIsh)
        assert cli.main(["q", str(project)]) == 0
        assert FakeIsh.made == [project]
        assert not path.exists()

    def test_a_server_that_hangs_up_falls_back(
        self, project: Path, monkeypatch
    ) -> None:
        path = socket_for(project)
        path.parent.mkdir(parents=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen()

        def hang_up() -> None:
            connection, _address = listener.accept()
            connection.close()

        thread = threading.Thread(target=hang_up, daemon=True)
        thread.start()
        try:
            monkeypatch.setattr(cli, "Ish", FakeIsh)
            assert cli.main(["q", str(project)]) == 0
            assert FakeIsh.made == [project]
        finally:
            thread.join(5)
            listener.close()
