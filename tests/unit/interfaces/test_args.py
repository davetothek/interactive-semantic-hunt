"""Test CLI argument parsing."""

from pathlib import Path

import pytest

from ish.interfaces.cli.args import CliArgs
from ish.settings import Settings


def test_default_args():
    args = CliArgs.from_args([])
    assert args.path == Path(".").resolve()
    assert args.query == ""
    assert args.interactive is False
    assert args.settings == Settings()


def test_custom_path():
    args = CliArgs.from_args(["", "src/ish"])
    assert args.path == Path("src/ish").resolve()
    assert args.query == ""


def test_verbosity_flags():
    assert CliArgs.from_args(["-v"]).settings.verbosity == 1
    assert CliArgs.from_args(["-vv"]).settings.verbosity == 2


def test_color_flag():
    assert CliArgs.from_args(["--color=never"]).settings.color == "never"
    assert CliArgs.from_args(["--color=always"]).settings.color == "always"
    assert CliArgs.from_args([]).settings.color == "auto"


def test_limit_flag():
    assert CliArgs.from_args(["--limit", "17"]).settings.limit == 17


def test_ignore_flag():
    args = CliArgs.from_args(["--ignore", "build", "dist"])
    assert args.settings.ignore == ("build", "dist")


def test_cli_beats_project_config(tmp_path, monkeypatch):
    """Confirm a flag overrides the same key in ish.toml."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ish.toml").write_text("limit = 3\n")

    assert CliArgs.from_args(["q", "."]).settings.limit == 3
    assert CliArgs.from_args(["q", ".", "--limit", "8"]).settings.limit == 8


def test_path_like_query_becomes_path(tmp_path, monkeypatch):
    """A single positional written as a path scans that path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()

    args = CliArgs.from_args(["src/"])
    assert args.path == (tmp_path / "src").resolve()
    assert args.query == ""


def test_py_file_query_becomes_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("pass\n")

    args = CliArgs.from_args(["app.py"])
    assert args.path == (tmp_path / "app.py").resolve()
    assert args.query == ""


def test_bare_word_stays_query(tmp_path, monkeypatch):
    """A bare word stays a query even when a directory of that name exists."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "log").mkdir()

    args = CliArgs.from_args(["log"])
    assert args.query == "log"
    assert args.path == tmp_path.resolve()


def test_missing_path_like_query_stays_query(tmp_path, monkeypatch):
    """A path-shaped value that does not exist stays a query."""
    monkeypatch.chdir(tmp_path)

    args = CliArgs.from_args(["auth/session handling"])
    assert args.query == "auth/session handling"


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc_info:
        CliArgs.from_args(["--version"])

    assert exc_info.value.code == 0
    assert "ish" in capsys.readouterr().out
