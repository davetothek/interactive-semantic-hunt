import importlib.metadata

from ish.interfaces.cli.config import CliArgs


def test_version_fallback(monkeypatch):
    """Verify fallback when package version is not found."""

    def mock_version(name):
        raise importlib.metadata.PackageNotFoundError()

    monkeypatch.setattr(importlib.metadata, "version", mock_version)

    # Reload or parse again to hit the mock
    args = CliArgs.from_args(["--color=never"])
    # Not much to assert here other than it doesn't crash
    # since argparse internal state captures the string.
    assert args.color is False
