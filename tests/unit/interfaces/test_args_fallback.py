import importlib.metadata

from ish.interfaces.cli.args import CliArgs


def test_version_fallback(monkeypatch):
    """Verify the parser still builds when the package version is absent."""

    def mock_version(name):
        raise importlib.metadata.PackageNotFoundError()

    monkeypatch.setattr(importlib.metadata, "version", mock_version)

    args = CliArgs.from_args(["--color=never"])
    assert args.settings.color == "never"
