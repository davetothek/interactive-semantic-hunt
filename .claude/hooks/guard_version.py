"""Keep a version bump on a release branch.

PreToolUse on Edit and Write. A change to ``version =`` in
``pyproject.toml`` is a release, and a release is its own PR from a
``release/*`` or ``hotfix/*`` branch. On any other branch the edit is
denied, and the reason names the procedure.

A branch that cannot be read is not a release branch.
"""

import re
import subprocess
import sys
from pathlib import Path

from _common import Decision, allow, answer, deny, read_input

HOOK = "guard_version"

RELEASE_BRANCH = re.compile(r"^(release|hotfix)/")
_VERSION = re.compile(r'^version\s*=\s*"([^"]*)"', re.MULTILINE)


def judge(tool_name: str, tool_input: dict, branch: str | None) -> Decision:
    """Return the decision for one edit, given the branch it happens on."""
    path = tool_input.get("file_path", "")
    if Path(str(path)).name != "pyproject.toml":
        return allow()

    before, after = _versions(tool_name, tool_input)
    if before is None or after is None or before == after:
        return allow()

    if branch is not None and RELEASE_BRANCH.match(branch):
        return allow()

    where = f"branch {branch!r}" if branch else "a detached HEAD"
    return deny(
        f"This edit bumps the version from {before} to {after} on {where}. "
        f"A version bump is its own PR from a release/* or hotfix/* branch, "
        f"merged with a merge commit. Use the release skill."
    )


def _versions(tool_name: str, tool_input: dict) -> tuple[str | None, str | None]:
    """Return the version before and after the edit, when both can be read."""
    current = _read(tool_input.get("file_path", ""))
    if tool_name == "Write":
        return _version_in(current), _version_in(str(tool_input.get("content", "")))

    old = str(tool_input.get("old_string", ""))
    new = str(tool_input.get("new_string", ""))
    if current is not None and old in current:
        return _version_in(current), _version_in(current.replace(old, new, 1))
    # The file cannot be read. Judge the replacement on its own.
    return _version_in(old), _version_in(new)


def _version_in(text: str | None) -> str | None:
    if text is None:
        return None
    found = _VERSION.search(text)
    return found.group(1) if found else None


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def current_branch(cwd: str) -> str | None:
    """Return the branch checked out in *cwd*, or None when there is none."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else None


def main() -> int:
    data = read_input()
    if data is None or data.get("tool_name") not in ("Edit", "Write"):
        return 0
    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    branch = current_branch(str(data.get("cwd", ".")))
    return answer(judge(str(data["tool_name"]), tool_input, branch), HOOK)


if __name__ == "__main__":
    sys.exit(main())
