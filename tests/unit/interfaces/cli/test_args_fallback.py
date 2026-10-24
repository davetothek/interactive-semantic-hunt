import importlib.metadata

import pytest

from ish.interfaces.cli.args import CliArgs


def test_version_fallback(monkeypatch, capsys):
    """Verify that --version names an absent package version as unknown."""

    def mock_version(name):
        raise importlib.metadata.PackageNotFoundError()

    monkeypatch.setattr(importlib.metadata, "version", mock_version)

    with pytest.raises(SystemExit) as exc_info:
        CliArgs.from_args(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == "ish unknown\n"
