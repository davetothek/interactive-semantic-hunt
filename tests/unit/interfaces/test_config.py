"""Test CLI configuration parsing."""

from pathlib import Path

from ish.interfaces.cli.config import CliArgs


def test_default_args(monkeypatch):
    import sys

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    args = CliArgs.from_args([])
    assert args.path == Path(".").resolve()
    assert args.verbosity == 0
    assert args.color is True
    assert args.query == ""
    assert args.embedder == "llama.cpp"
    assert args.interactive is False


def test_custom_path():
    args = CliArgs.from_args(["", "src/ish"])
    assert args.path == Path("src/ish").resolve()
    assert args.query == ""


def test_verbosity_flags():
    args = CliArgs.from_args(["-v"])
    assert args.verbosity == 1

    args = CliArgs.from_args(["-vv"])
    assert args.verbosity == 2


def test_color_never():
    args = CliArgs.from_args(["--color=never"])
    assert args.color is False


def test_color_always():
    args = CliArgs.from_args(["--color=always"])
    assert args.color is True


def test_color_auto_not_tty(monkeypatch):
    import sys

    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    args = CliArgs.from_args([])
    assert args.color is False


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
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        CliArgs.from_args(["--version"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "ish" in out
