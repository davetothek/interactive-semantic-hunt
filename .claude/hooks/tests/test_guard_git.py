"""Test the git guard: each blocked command beside its nearest allowed one."""

import json
import subprocess
import sys
from pathlib import Path

import guard_git
import pytest
from _common import ALLOW, ASK, DENY

HOOK = Path(guard_git.__file__)


@pytest.mark.parametrize(
    ("command", "kind", "why"),
    [
        # Pushing to the default branch.
        ("git push origin main", DENY, "main"),
        ("git push origin master", DENY, "master"),
        ("git push origin HEAD:main", DENY, "main"),
        ("git push -u origin main", DENY, "main"),
        ("git push origin feature/x", ALLOW, ""),
        ("git push -u origin claude/branch", ALLOW, ""),
        ("git push", ALLOW, ""),
        # Pushing a tag publishes. Outside a repository no tag can be checked.
        ("git push origin v0.2.0", DENY, "cannot find the tag"),
        ("git push origin refs/tags/v1.0.0", DENY, "cannot find the tag"),
        ("git push --tags", DENY, "PyPI"),
        ("git push --follow-tags origin main", DENY, ""),
        ("git tag v0.2.0", ALLOW, ""),
        # Force.
        ("git push --force origin feature", DENY, "force"),
        ("git push -f origin feature", DENY, "force"),
        ("git push -fu origin feature", DENY, "force"),
        ("git push --force-with-lease origin feature", ASK, "lease"),
        ("git push --force-with-lease=main origin feature", ASK, "lease"),
        # Discarding work.
        ("git checkout -- src/ish/x.py", DENY, "discards"),
        ("git checkout HEAD -- src/ish/x.py", DENY, "discards"),
        ("git checkout .", DENY, "discards"),
        ("git checkout main", ALLOW, ""),
        ("git checkout -b feature", ALLOW, ""),
        ("git restore src/ish/x.py", DENY, "discards"),
        ("git restore --staged src/ish/x.py", ALLOW, ""),
        ("git restore --staged --worktree src/ish/x.py", DENY, "discards"),
        ("git reset --hard HEAD~1", DENY, "discards"),
        ("git reset HEAD~1", ALLOW, ""),
        ("git reset --soft HEAD~1", ALLOW, ""),
        ("git stash drop", DENY, "stash"),
        ("git stash clear", DENY, "stash"),
        ("git stash", ALLOW, ""),
        ("git stash pop", ALLOW, ""),
        ("git clean -fd", DENY, "clean"),
        ("git clean -n", ALLOW, ""),
        # Dependencies ask.
        ("uv add requests", ASK, "dependency"),
        ("uv remove requests", ASK, "dependency"),
        ("uv pip install requests", ASK, "dependency"),
        ("pip install requests", ASK, "dependency"),
        ("uv sync --dev", ALLOW, ""),
        ("uv run poe check", ALLOW, ""),
        # Not git at all.
        ("ls -la", ALLOW, ""),
        ("echo 'git push origin main'", ALLOW, ""),
        ("", ALLOW, ""),
    ],
)
def test_one_command(command: str, kind: str, why: str, tmp_path: Path) -> None:
    decision = guard_git.judge(command, str(tmp_path))
    assert decision.kind == kind, decision.reason
    assert why.lower() in decision.reason.lower()


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("cd /tmp && git push origin main", DENY),
        ("git fetch; git push origin main", DENY),
        ("git status | cat; git checkout -- x.py", DENY),
        ("GIT_TRACE=1 git push origin main", DENY),
        ("git -C /repo push origin main", DENY),
        ("git fetch && uv add x && git push origin feature", ASK),
        ("cd /tmp && git push origin feature && git status", ALLOW),
        ("git push origin feature\ngit push origin main", DENY),
    ],
)
def test_compound_commands_are_judged_by_segment(command: str, kind: str) -> None:
    assert guard_git.judge(command).kind == kind


def test_an_unbalanced_quote_is_still_judged() -> None:
    assert guard_git.judge("git push origin main 'oops").kind == DENY


def test_deny_beats_ask_beats_allow() -> None:
    assert guard_git.judge("uv add x && git push origin main").kind == DENY
    assert guard_git.judge("ls && uv add x").kind == ASK


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


class TestProcess:
    """Verify the exit codes and output Claude Code reads."""

    def test_deny_exits_two_with_reason_on_stderr(self) -> None:
        result = _run(
            {"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
        )
        assert result.returncode == 2
        assert "guard_git" in result.stderr
        assert result.stdout == ""

    def test_ask_exits_zero_with_json_decision(self) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": "uv add x"}})
        assert result.returncode == 0
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        assert "dependency" in output["permissionDecisionReason"]

    def test_allow_exits_zero_silently(self) -> None:
        result = _run({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_another_tool_is_ignored(self) -> None:
        result = _run(
            {"tool_name": "Edit", "tool_input": {"command": "git push origin main"}}
        )
        assert result.returncode == 0

    def test_garbage_input_allows(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(where: Path, version: str) -> None:
    (where / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    _git(where, "add", "pyproject.toml")
    _git(where, "commit", "-q", "-m", f"Release {version}")


@pytest.fixture
def released(tmp_path: Path) -> Path:
    """Return a clone whose origin/main holds the commit tagged v1.2.3."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "switch", "-q", "-c", "main")
    _commit(clone, "1.2.3")
    _git(clone, "tag", "v1.2.3")
    _git(clone, "push", "-q", "origin", "main")
    _git(clone, "fetch", "-q", "origin")
    return clone


class TestReleaseTagPush:
    """A release tag on origin/main may be pushed. Nothing else that tags."""

    def test_merged_release_tag_is_allowed(self, released: Path) -> None:
        decision = guard_git.judge("git push origin v1.2.3", str(released))
        assert decision.kind == ALLOW, decision.reason

    def test_full_tag_ref_is_allowed(self, released: Path) -> None:
        decision = guard_git.judge("git push origin refs/tags/v1.2.3", str(released))
        assert decision.kind == ALLOW, decision.reason

    def test_tag_not_on_main_is_denied(self, released: Path) -> None:
        _git(released, "switch", "-q", "-c", "release/1.2.4")
        _commit(released, "1.2.4")
        _git(released, "tag", "v1.2.4")
        decision = guard_git.judge("git push origin v1.2.4", str(released))
        assert decision.kind == DENY
        assert "not on origin/main" in decision.reason

    def test_tag_that_misnames_the_version_is_denied(self, released: Path) -> None:
        _git(released, "tag", "v1.2.4")
        decision = guard_git.judge("git push origin v1.2.4", str(released))
        assert decision.kind == DENY
        assert "does not declare version 1.2.4" in decision.reason

    def test_missing_tag_is_denied(self, released: Path) -> None:
        decision = guard_git.judge("git push origin v9.9.9", str(released))
        assert decision.kind == DENY
        assert "cannot find the tag" in decision.reason.lower()

    @pytest.mark.parametrize(
        "command",
        [
            "git push origin v1.2.3 feature",
            "git push origin v1.2.3 v1.2.3",
            "git push upstream v1.2.3",
            "git push --tags origin",
            "git push --follow-tags origin v1.2.3",
        ],
    )
    def test_anything_beside_one_tag_to_origin_is_denied(
        self, released: Path, command: str
    ) -> None:
        decision = guard_git.judge(command, str(released))
        assert decision.kind == DENY
        assert "PyPI" in decision.reason

    @pytest.mark.parametrize("tag", ["v1.2", "v1.2.3-rc1", "refs/tags/latest"])
    def test_a_tag_that_is_not_a_release_is_denied(
        self, released: Path, tag: str
    ) -> None:
        decision = guard_git.judge(f"git push origin {tag}", str(released))
        assert decision.kind == DENY
        assert "not a release tag" in decision.reason

    def test_dash_c_names_the_repository(self, released: Path) -> None:
        command = f"git -C {released} push origin v1.2.3"
        decision = guard_git.judge(command, str(released.parent))
        assert decision.kind == ALLOW, decision.reason

    def test_a_cd_before_the_push_is_denied(self, released: Path) -> None:
        command = f"cd {released} && git push origin v1.2.3"
        decision = guard_git.judge(command, str(released))
        assert decision.kind == DENY
        assert "cd" in decision.reason

    def test_git_that_cannot_run_is_denied(
        self, released: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_args: object, **_kwargs: object) -> None:
            raise OSError("no git")

        monkeypatch.setattr(guard_git.subprocess, "run", broken)
        decision = guard_git.judge("git push origin v1.2.3", str(released))
        assert decision.kind == DENY

    def test_the_process_reads_cwd_from_its_input(self, released: Path) -> None:
        result = _run(
            {
                "tool_name": "Bash",
                "cwd": str(released),
                "tool_input": {"command": "git push origin v1.2.3"},
            }
        )
        assert result.returncode == 0, result.stderr
