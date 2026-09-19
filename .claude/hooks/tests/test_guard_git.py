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
        # Pushing a tag publishes.
        ("git push origin v0.2.0", DENY, "PyPI"),
        ("git push origin refs/tags/v1.0.0", DENY, "PyPI"),
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
def test_one_command(command: str, kind: str, why: str) -> None:
    decision = guard_git.judge(command)
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
