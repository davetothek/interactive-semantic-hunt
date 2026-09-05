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


class TestIsAfter:
    """Verify which way round two versions stand."""

    def test_a_later_patch_comes_after(self) -> None:
        assert release.is_after("0.1.2", "0.1.1")

    def test_a_later_minor_comes_after(self) -> None:
        assert release.is_after("0.2.0", "0.1.9")

    def test_the_same_version_comes_after_nothing(self) -> None:
        assert not release.is_after("0.1.1", "0.1.1")

    def test_an_earlier_version_does_not(self) -> None:
        assert not release.is_after("0.1.0", "0.2.0")


@pytest.fixture()
def repository(tmp_path, monkeypatch):
    """Build a throwaway repository declaring 0.1.1, and work inside it."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.1"\n')
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n"
        "\n"
        "## Unreleased\n"
        "\n"
        "### Fixed\n"
        "\n"
        "- Stop the crash.\n"
        "\n"
        "## 0.1.1 - 2026-09-05\n"
        "\n"
        "- Shipped.\n"
    )
    monkeypatch.chdir(tmp_path)
    release.git("init", "-q")
    release.git("config", "user.email", "test@example.com")
    release.git("config", "user.name", "Test")
    release.git("add", ".")
    release.git("commit", "-qm", "First")
    # Neither belongs in a test: one reaches the network, the other runs
    # the whole suite again.
    monkeypatch.setattr(release, "lock", lambda: None)
    monkeypatch.setattr(release, "check", lambda: None)
    return tmp_path


class TestCut:
    """Verify what a release writes, commits, and tags."""

    def test_a_version_already_declared_is_tagged_not_raised(self, repository) -> None:
        """Tag a release that was prepared and never tagged.

        Committing is what used to fail here: nothing had changed, so git
        refused, and the release could not be cut at all.
        """
        before = release.git("rev-parse", "HEAD")

        assert release.cut("0.1.1") == "0.1.1"

        assert release.git("tag", "--list") == "v0.1.1"
        assert release.git("rev-parse", "HEAD") == before, "committed with no change"
        assert 'version = "0.1.1"' in (repository / "pyproject.toml").read_text()

    def test_a_leading_v_names_the_same_version(self, repository) -> None:
        assert release.cut("v0.1.1") == "0.1.1"
        assert release.git("tag", "--list") == "v0.1.1"

    def test_raising_the_patch_writes_and_commits(self, repository) -> None:
        before = release.git("rev-parse", "HEAD")

        assert release.cut(None) == "0.1.2"

        assert release.git("tag", "--list") == "v0.1.2"
        assert release.git("rev-parse", "HEAD") != before
        assert release.git("log", "-1", "--pretty=%s") == "Release 0.1.2"
        assert 'version = "0.1.2"' in (repository / "pyproject.toml").read_text()

    def test_refuses_to_go_backwards(self, repository) -> None:
        with pytest.raises(release.ReleaseError, match="does not come after"):
            release.cut("0.1.0")
        assert release.git("tag", "--list") == ""

    def test_refuses_a_tag_that_exists(self, repository) -> None:
        release.git("tag", "v0.2.0")
        with pytest.raises(release.ReleaseError, match="exists already"):
            release.cut("0.2.0")

    def test_refuses_a_working_tree_with_changes(self, repository) -> None:
        (repository / "pyproject.toml").write_text('[project]\nversion = "0.9.9"\n')
        with pytest.raises(release.ReleaseError, match="commit or stash"):
            release.cut(None)

    def test_a_failing_check_writes_nothing(self, repository, monkeypatch) -> None:
        """Leave the tree as it was found when the checks do not pass."""

        def boom() -> None:
            raise release.ReleaseError("poe check failed")

        monkeypatch.setattr(release, "check", boom)

        with pytest.raises(release.ReleaseError, match="poe check failed"):
            release.cut(None)

        assert 'version = "0.1.1"' in (repository / "pyproject.toml").read_text()
        assert release.git("tag", "--list") == ""
        assert release.git("status", "--porcelain") == ""


class TestTheRealProjectFile:
    """Verify the script reads the file it is actually pointed at."""

    def test_reads_this_project_s_version(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
        assert release.SEMVER.match(release.read_version(text))


class TestSection:
    """Verify the changelog entry a version answers to."""

    CHANGELOG = (
        "# Changelog\n"
        "\n"
        "## Unreleased\n"
        "\n"
        "- Not out yet.\n"
        "\n"
        "## 0.1.10 - 2026-10-01\n"
        "\n"
        "- The tenth patch.\n"
        "\n"
        "## 0.1.1 - 2026-09-05\n"
        "\n"
        "- The first patch.\n"
    )

    def test_reads_the_entry_under_a_version(self) -> None:
        assert release.section(self.CHANGELOG, "## 0.1.1") == "- The first patch."

    def test_stops_at_the_next_heading(self) -> None:
        assert release.section(self.CHANGELOG, "## Unreleased") == "- Not out yet."

    def test_a_shorter_version_does_not_answer_for_a_longer_one(self) -> None:
        """Keep 0.1.1 from reading 0.1.10's entry.

        The two headings share a prefix, so a match that ignored what
        follows the version would take whichever came first.
        """
        assert release.section(self.CHANGELOG, "## 0.1.10") == "- The tenth patch."

    def test_reads_the_last_entry_to_the_end_of_the_file(self) -> None:
        assert release.section(self.CHANGELOG, "## 0.1.1").endswith("patch.")

    def test_a_version_with_no_entry_reads_as_nothing(self) -> None:
        assert release.section(self.CHANGELOG, "## 9.9.9") == ""


class TestPromote:
    """Verify what naming an Unreleased heading writes."""

    STARTING = "# Changelog\n\n## Unreleased\n\n- Stop the crash.\n"

    def test_names_the_entries_for_the_version(self) -> None:
        after = release.promote(self.STARTING, "0.1.2", "2026-09-05")
        assert "## 0.1.2 - 2026-09-05" in after
        assert release.section(after, "## 0.1.2") == "- Stop the crash."

    def test_leaves_an_empty_unreleased_heading_above(self) -> None:
        """Give the next change somewhere to go without anyone making it."""
        after = release.promote(self.STARTING, "0.1.2", "2026-09-05")
        assert release.section(after, "## Unreleased") == ""
        assert after.index("## Unreleased") < after.index("## 0.1.2")

    def test_refuses_a_changelog_with_no_unreleased_heading(self) -> None:
        with pytest.raises(release.ReleaseError, match="no ## Unreleased"):
            release.promote("# Changelog\n", "0.1.2", "2026-09-05")

    def test_refuses_an_unreleased_heading_holding_nothing(self) -> None:
        """A release that says nothing is a forgotten line, not an empty release."""
        with pytest.raises(release.ReleaseError, match="nothing under"):
            release.promote(
                "# Changelog\n\n## Unreleased\n\n## 0.1.1 - 2026-09-05\n\n- Old.\n",
                "0.1.2",
                "2026-09-05",
            )

    def test_names_only_the_first_unreleased_heading(self) -> None:
        after = release.promote(self.STARTING, "0.1.2", "2026-09-05")
        assert after.count("## Unreleased") == 1


class TestCutWritesTheChangelog:
    """Verify what a release does to CHANGELOG.md."""

    def test_names_the_entries_for_the_version_it_cuts(self, repository) -> None:
        release.cut("0.1.2")
        text = (repository / "CHANGELOG.md").read_text()
        assert release.section(text, "## 0.1.2") == "### Fixed\n\n- Stop the crash."
        assert release.section(text, "## Unreleased") == ""

    def test_refuses_a_release_that_says_nothing(self, repository) -> None:
        (repository / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n")
        release.git("commit", "-qam", "Empty the changelog")
        with pytest.raises(release.ReleaseError, match="nothing under"):
            release.cut("0.1.2")

    def test_a_release_that_says_nothing_writes_no_version(self, repository) -> None:
        """Leave pyproject.toml alone when the changelog stops the release."""
        (repository / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n")
        release.git("commit", "-qam", "Empty the changelog")
        with pytest.raises(release.ReleaseError):
            release.cut("0.1.2")
        assert 'version = "0.1.1"' in (repository / "pyproject.toml").read_text()

    def test_refuses_a_missing_changelog(self, repository) -> None:
        (repository / "CHANGELOG.md").unlink()
        release.git("commit", "-qam", "Drop the changelog")
        with pytest.raises(release.ReleaseError, match="CHANGELOG.md is missing"):
            release.cut("0.1.2")

    def test_a_declared_version_needs_an_entry_of_its_own(self, repository) -> None:
        """Refuse to tag a prepared release whose changelog was never named."""
        (repository / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Unreleased\n\n- Stop the crash.\n"
        )
        release.git("commit", "-qam", "Take the entry away")
        with pytest.raises(release.ReleaseError, match="no entry for 0.1.1"):
            release.cut("0.1.1")

    def test_a_declared_version_with_an_entry_is_tagged(self, repository) -> None:
        assert release.cut("0.1.1") == "0.1.1"
        assert release.git("tag", "--list", "v0.1.1") == "v0.1.1"

    def test_a_declared_version_leaves_the_changelog_alone(self, repository) -> None:
        """Name nothing again: the entry was written when the version was set."""
        before = (repository / "CHANGELOG.md").read_text()
        release.cut("0.1.1")
        assert (repository / "CHANGELOG.md").read_text() == before


class TestNotes:
    """Verify the entry the release workflow takes for a release body."""

    def test_prints_the_entry_for_a_version(self, repository, capsys) -> None:
        (repository / "CHANGELOG.md").write_text(
            "# Changelog\n\n## 0.1.1 - 2026-09-05\n\n- Shipped.\n"
        )
        assert release.main(["--notes", "0.1.1"]) == 0
        assert capsys.readouterr().out.strip() == "- Shipped."

    def test_a_leading_v_names_the_same_version(self, repository, capsys) -> None:
        """Take the tag name as the workflow has it, without trimming it first."""
        (repository / "CHANGELOG.md").write_text(
            "# Changelog\n\n## 0.1.1 - 2026-09-05\n\n- Shipped.\n"
        )
        assert release.main(["--notes", "v0.1.1"]) == 0
        assert capsys.readouterr().out.strip() == "- Shipped."

    def test_reports_a_version_the_changelog_does_not_name(self, repository) -> None:
        assert release.main(["--notes", "9.9.9"]) == 1
