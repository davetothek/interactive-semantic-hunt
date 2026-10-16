"""Test asking git which repository a tree belongs to."""

import subprocess
from pathlib import Path

import pytest

from ish.adapters.vcs.git import Repository, repository_of


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True
    )


@pytest.fixture()
def repo(tmp_path_factory) -> Path:
    """A repository with one commit, so a worktree can be added to it."""
    root = tmp_path_factory.mktemp("repo")
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    (root / "app.py").write_text("pass\n")
    (root / "sub").mkdir()
    (root / "sub" / "deep.py").write_text("pass\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    return root


@pytest.fixture()
def worktree(repo: Path, tmp_path_factory) -> Path:
    """A second checkout of the same repository, as `git worktree add` makes it."""
    where = tmp_path_factory.mktemp("worktrees") / "feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(where))
    return where


class TestInsideARepository:
    """Verify the answers git gives."""

    def test_the_main_checkout_names_itself(self, repo: Path) -> None:
        found = repository_of(repo)
        assert found == Repository(
            common_dir=(repo / ".git").resolve(), checkout=repo.resolve()
        )

    def test_a_subdirectory_names_its_checkout(self, repo: Path) -> None:
        found = repository_of(repo / "sub")
        assert found is not None
        assert found.checkout == repo.resolve()
        assert found.common_dir == (repo / ".git").resolve()

    def test_a_worktree_shares_the_repository(self, repo: Path, worktree: Path) -> None:
        """This is the whole claim: two checkouts, one common directory."""
        main = repository_of(repo)
        other = repository_of(worktree)
        assert main is not None and other is not None
        assert other.common_dir == main.common_dir
        assert other.checkout == worktree.resolve()
        assert other.checkout != main.checkout

    def test_a_directory_inside_a_worktree(self, worktree: Path) -> None:
        found = repository_of(worktree / "sub")
        assert found is not None
        assert found.checkout == worktree.resolve()


class TestOutsideARepository:
    """Verify the answer is None wherever git cannot help."""

    def test_a_plain_directory(self, tmp_path: Path) -> None:
        assert repository_of(tmp_path) is None

    def test_missing_git(self, repo: Path, monkeypatch) -> None:
        def unavailable(*args, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "run", unavailable)
        assert repository_of(repo) is None

    def test_a_timeout(self, repo: Path, monkeypatch) -> None:
        def slow(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=10)

        monkeypatch.setattr(subprocess, "run", slow)
        assert repository_of(repo) is None

    def test_an_answer_with_a_line_missing(self, repo: Path, monkeypatch) -> None:
        class Short:
            returncode = 0
            stdout = ".git\n"
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Short())
        assert repository_of(repo) is None
