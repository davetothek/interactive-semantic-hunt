"""Test the git-aware file filter."""

import subprocess
from pathlib import Path

import pytest

from ish.adapters.vcs.git import GitVisibleFiles


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".gitignore").write_text("build/\n*.log\n")
    (tmp_path / "app.py").write_text("pass\n")
    (tmp_path / "notes.log").write_text("noise\n")
    build = tmp_path / "build"
    build.mkdir()
    (build / "gen.py").write_text("pass\n")
    git(tmp_path, "add", "app.py", ".gitignore")
    git(tmp_path, "commit", "-qm", "first")
    return tmp_path


class TestInsideARepository:
    """Verify the answers git gives."""

    def test_tracked_file_is_visible(self, repo: Path) -> None:
        assert GitVisibleFiles(repo).ignores(repo / "app.py") is False

    def test_ignored_directory(self, repo: Path) -> None:
        assert GitVisibleFiles(repo).ignores(repo / "build" / "gen.py") is True

    def test_ignored_suffix(self, repo: Path) -> None:
        assert GitVisibleFiles(repo).ignores(repo / "notes.log") is True

    def test_untracked_but_not_ignored_is_visible(self, repo: Path) -> None:
        """A file just written must be indexed before it is committed."""
        (repo / "fresh.py").write_text("pass\n")
        assert GitVisibleFiles(repo).ignores(repo / "fresh.py") is False

    def test_nested_gitignore_is_honored(self, repo: Path) -> None:
        """Reimplementing the rules would miss this; git does not."""
        nested = repo / "pkg"
        nested.mkdir()
        (nested / ".gitignore").write_text("secret.py\n")
        (nested / "secret.py").write_text("pass\n")
        (nested / "open.py").write_text("pass\n")

        visible = GitVisibleFiles(repo)
        assert visible.ignores(nested / "secret.py") is True
        assert visible.ignores(nested / "open.py") is False

    def test_the_repository_is_read_once(self, repo: Path, monkeypatch) -> None:
        """The scan asks about every file, so the answer must be cached."""
        visible = GitVisibleFiles(repo)
        visible.ignores(repo / "app.py")

        calls: list[list[str]] = []
        real = subprocess.run

        def counted(command, **kwargs):
            calls.append(command)
            return real(command, **kwargs)

        monkeypatch.setattr(subprocess, "run", counted)
        for _ in range(20):
            visible.ignores(repo / "app.py")
        assert calls == []


class TestOutsideARepository:
    """Verify that the filter is inert where git cannot help."""

    def test_plain_directory_ignores_nothing(self, tmp_path: Path) -> None:
        assert GitVisibleFiles(tmp_path).ignores(tmp_path / "a.py") is False

    def test_path_outside_the_repository(self, repo: Path, tmp_path_factory) -> None:
        elsewhere = tmp_path_factory.mktemp("elsewhere") / "x.py"
        assert GitVisibleFiles(repo).ignores(elsewhere) is False

    def test_missing_git_is_not_fatal(self, repo: Path, monkeypatch) -> None:
        def unavailable(*args, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "run", unavailable)
        assert GitVisibleFiles(repo).ignores(repo / "build" / "gen.py") is False

    def test_a_failing_command_is_not_fatal(self, repo: Path, monkeypatch) -> None:
        class Failed:
            returncode = 128
            stdout = ""
            stderr = "fatal: not a git repository"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())
        assert GitVisibleFiles(repo).ignores(repo / "app.py") is False

    def test_a_timeout_is_not_fatal(self, repo: Path, monkeypatch) -> None:
        def slow(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=10)

        monkeypatch.setattr(subprocess, "run", slow)
        assert GitVisibleFiles(repo).ignores(repo / "app.py") is False


def test_a_second_call_reuses_the_cached_answer(repo: Path) -> None:
    """The loader returns the same set rather than re-reading."""
    visible = GitVisibleFiles(repo)
    first = visible._load()
    assert first is not None
    assert visible._load() is first


def test_a_failure_listing_files_is_not_fatal(repo: Path, monkeypatch) -> None:
    """Finding the repository may succeed while listing it fails."""
    real = subprocess.run

    def selective(command, **kwargs):
        if "ls-files" in command:

            class Failed:
                returncode = 128
                stdout = ""
                stderr = "fatal: index corrupt"

            return Failed()
        return real(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", selective)
    assert GitVisibleFiles(repo).ignores(repo / "build" / "gen.py") is False


class TestScanningASubdirectory:
    """Verify the answers hold when the scan root is below the repository.

    `git ls-files` reports paths relative to the working directory, so a
    subdirectory scan resolved every path against the wrong base and
    reported the whole tree as ignored.
    """

    @pytest.fixture()
    def nested(self, repo: Path) -> Path:
        package = repo / "pkg"
        package.mkdir()
        (package / "real.py").write_text("pass\n")
        (package / "junk.log").write_text("noise\n")
        git(repo, "add", "pkg/real.py")
        git(repo, "commit", "-qm", "nested")
        return package

    def test_tracked_file_below_the_root_is_visible(self, nested: Path) -> None:
        assert GitVisibleFiles(nested).ignores(nested / "real.py") is False

    def test_ignored_file_below_the_root_is_ignored(self, nested: Path) -> None:
        assert GitVisibleFiles(nested).ignores(nested / "junk.log") is True

    def test_a_subdirectory_scan_sees_its_files(self, nested: Path) -> None:
        """The whole point: the scan must not report everything ignored."""
        visible = GitVisibleFiles(nested)
        assert visible.ignores(nested / "real.py") is False
