"""Test the version guard: a bump belongs on a release branch."""

import json
import subprocess
import sys
from pathlib import Path

import guard_version
import pytest
from _common import ALLOW, DENY

HOOK = Path(guard_version.__file__)

PYPROJECT = '[project]\nname = "ish"\nversion = "0.2.1"\nrequires-python = ">=3.12"\n'


@pytest.fixture()
def pyproject(tmp_path: Path) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    return path


def edit(path: Path, old: str, new: str) -> dict:
    return {"file_path": str(path), "old_string": old, "new_string": new}


class TestEdit:
    def test_a_bump_on_a_feature_branch_is_denied(self, pyproject: Path) -> None:
        change = edit(pyproject, 'version = "0.2.1"', 'version = "0.3.0"')
        decision = guard_version.judge("Edit", change, "claude/feature")
        assert decision.kind == DENY
        assert "0.2.1 to 0.3.0" in decision.reason
        assert "release/*" in decision.reason

    @pytest.mark.parametrize("branch", ["release/0.3.0", "hotfix/0.2.2"])
    def test_a_bump_on_a_release_branch_is_allowed(self, pyproject, branch) -> None:
        change = edit(pyproject, 'version = "0.2.1"', 'version = "0.3.0"')
        assert guard_version.judge("Edit", change, branch).kind == ALLOW

    def test_a_bump_with_no_branch_is_denied(self, pyproject: Path) -> None:
        change = edit(pyproject, 'version = "0.2.1"', 'version = "0.3.0"')
        decision = guard_version.judge("Edit", change, None)
        assert decision.kind == DENY
        assert "detached" in decision.reason

    def test_an_edit_that_keeps_the_version_is_allowed(self, pyproject: Path) -> None:
        change = edit(
            pyproject, 'requires-python = ">=3.12"', 'requires-python = ">=3.13"'
        )
        assert guard_version.judge("Edit", change, "claude/feature").kind == ALLOW

    def test_another_file_is_ignored(self, tmp_path: Path) -> None:
        other = tmp_path / "notes.toml"
        change = edit(other, 'version = "1"', 'version = "2"')
        assert guard_version.judge("Edit", change, "claude/feature").kind == ALLOW

    def test_an_unreadable_file_is_judged_on_the_replacement(self, tmp_path) -> None:
        missing = tmp_path / "pyproject.toml"
        change = edit(missing, 'version = "0.2.1"', 'version = "0.3.0"')
        assert guard_version.judge("Edit", change, "claude/feature").kind == DENY


class TestWrite:
    def test_a_rewrite_that_bumps_is_denied(self, pyproject: Path) -> None:
        content = PYPROJECT.replace('"0.2.1"', '"0.3.0"')
        change = {"file_path": str(pyproject), "content": content}
        assert guard_version.judge("Write", change, "claude/feature").kind == DENY

    def test_a_rewrite_that_keeps_the_version_is_allowed(self, pyproject: Path) -> None:
        change = {"file_path": str(pyproject), "content": PYPROJECT + "\n[tool.x]\n"}
        assert guard_version.judge("Write", change, "claude/feature").kind == ALLOW


class TestBranch:
    def test_the_branch_of_a_repository_is_read(self, tmp_path: Path) -> None:
        subprocess.run(
            ["git", "init", "-q", "-b", "release/9.9.9", str(tmp_path)], check=True
        )
        assert guard_version.current_branch(str(tmp_path)) == "release/9.9.9"

    def test_no_repository_means_no_branch(self, tmp_path: Path) -> None:
        assert guard_version.current_branch(str(tmp_path)) is None


class TestProcess:
    def test_deny_exits_two(self, pyproject: Path, tmp_path: Path) -> None:
        subprocess.run(
            ["git", "init", "-q", "-b", "claude/feature", str(tmp_path)], check=True
        )
        payload = {
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": edit(pyproject, 'version = "0.2.1"', 'version = "0.3.0"'),
        }
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "guard_version" in result.stderr

    def test_another_tool_is_ignored(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {}}),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
