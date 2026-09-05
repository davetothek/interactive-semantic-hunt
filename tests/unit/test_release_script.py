"""Test the release script's version arithmetic.

The script lives outside the package, because it is a tool for cutting a
release rather than something the wheel ships. Load it by path.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load():
    """Import the release script by path, as its own module."""
    spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load()


class TestNextPatch:
    """Verify the patch number a release with no version named takes."""

    def test_raises_the_last_number(self) -> None:
        assert release.next_patch("0.1.1") == "0.1.2"

    def test_carries_nothing_into_the_minor(self) -> None:
        assert release.next_patch("1.2.9") == "1.2.10"

    def test_refuses_a_version_it_cannot_read(self) -> None:
        with pytest.raises(release.ReleaseError, match="X.Y.Z"):
            release.next_patch("0.2.0rc1")


class TestReadVersion:
    """Verify the version the script takes from pyproject.toml."""

    def test_reads_the_declared_version(self) -> None:
        assert release.read_version('name = "x"\nversion = "0.1.1"\n') == "0.1.1"

    def test_refuses_a_file_declaring_none(self) -> None:
        with pytest.raises(release.ReleaseError, match="no version"):
            release.read_version('name = "x"\n')


class TestSetVersion:
    """Verify what a bump writes, and what it leaves alone."""

    def test_replaces_the_version(self) -> None:
        written = release.set_version('version = "0.1.1"\n', "0.2.0")
        assert written == 'version = "0.2.0"\n'

    def test_leaves_a_later_version_line_alone(self) -> None:
        """A dependency may name a version, and it is not this one."""
        text = 'version = "0.1.1"\n\n[tool.other]\nversion = "9.9.9"\n'
        written = release.set_version(text, "0.2.0")
        assert 'version = "0.2.0"' in written
        assert 'version = "9.9.9"' in written

    def test_refuses_a_file_declaring_none(self) -> None:
        with pytest.raises(release.ReleaseError, match="no version"):
            release.set_version('name = "x"\n', "0.2.0")


class TestTheRealProjectFile:
    """Verify the script reads the file it is actually pointed at."""

    def test_reads_this_project_s_version(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
        assert release.SEMVER.match(release.read_version(text))
