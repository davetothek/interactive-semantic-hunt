"""Stop a shell command that cannot be undone.

PreToolUse on Bash. Three groups of commands are judged:

- A push to the default branch and a forced push are denied. The first
  breaks the repository's own rule, and the second rewrites history.
- A push of a tag publishes to PyPI. It is allowed for one release tag
  ``vX.Y.Z`` alone, when its commit is on ``origin/main`` and its
  ``pyproject.toml`` declares ``X.Y.Z``. That is the last step of
  ``/release``, after its pull request merged. Every other tag push is
  denied, and so is one the hook cannot check.
- A command that discards uncommitted work is denied. A model once
  reverted its own unfinished rewrite with ``git checkout -- file``.
- A command that adds a dependency asks first. The repository's ground
  rule says so.

A compound command is judged one segment at a time, so ``cd x && git
push origin main`` is still a push to main.
"""

import os
import re
import shlex
import subprocess
import sys

from _common import Decision, allow, answer, ask, deny, read_input

HOOK = "guard_git"

PROTECTED_BRANCHES = frozenset({"main", "master"})

# A push target that names a tag. Tags in this repository are versions.
_TAG_LIKE = re.compile(r"^(refs/tags/|v\d)")

# The one tag a push may carry: a release, as ``/release`` names it.
_RELEASE_TAG = re.compile(r"^(?:refs/tags/)?(v(\d+\.\d+\.\d+))$")

# The version a ``pyproject.toml`` declares.
_VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)

RELEASE_REMOTE = "origin"
RELEASED_FROM = "origin/main"

# Seconds one git query may take before the tag counts as unchecked.
GIT_TIMEOUT = 5

# A command that moves the shell to another directory. After it, the
# hook cannot tell which repository a tag push reads.
_MOVES = frozenset({"cd", "pushd", "popd"})

# A short option cluster that carries ``-f``, such as ``-f`` or ``-fu``.
_FORCE_SHORT = re.compile(r"^-[a-zA-Z]*f[a-zA-Z]*$")

# Where one shell command ends and the next starts.
_SEGMENT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")

# A leading ``NAME=value`` that sets an environment variable.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# A redirection is no argument of the command. Its operator holds the
# target in the same word, as in 2>&1, or stands alone before it.
_REDIRECTION = re.compile(r"^(?:\d+|&)?(?:>>?|<)")
_REDIRECTION_ALONE = re.compile(r"^(?:\d+|&)?(?:>>?|<<?)$")

DEPENDENCY_COMMANDS = (
    ("uv", "add"),
    ("uv", "remove"),
    ("uv", "pip"),
    ("pip", "install"),
    ("pip3", "install"),
    ("poetry", "add"),
)


def judge(command: str, cwd: str | None = None) -> Decision:
    """Return the decision for one Bash command line run in *cwd*."""
    where = cwd or os.getcwd()
    moved = False
    worst = allow()
    for segment in _SEGMENT.split(command):
        verdict = _judge_segment(segment, None if moved else where)
        worst = _stricter(worst, verdict)
        tokens = _tokens(segment)
        moved = moved or bool(tokens and tokens[0] in _MOVES)
    return worst


def _stricter(first: Decision, second: Decision) -> Decision:
    """Return the decision that constrains more. Deny beats ask beats allow."""
    order = {"allow": 0, "ask": 1, "deny": 2}
    return second if order[second.kind] > order[first.kind] else first


def _judge_segment(segment: str, cwd: str | None) -> Decision:
    """Judge one command. A *cwd* of None means the directory is unknown."""
    tokens = _without_redirections(_tokens(segment))
    while tokens and _ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return allow()

    for prefix in DEPENDENCY_COMMANDS:
        if tuple(tokens[: len(prefix)]) == prefix:
            return ask("ask before adding a dependency")

    if tokens[0] != "git":
        return allow()

    # Skip ``git -C dir`` and other options before the subcommand.
    rest = _after_git_options(tokens[1:])
    if not rest:
        return allow()
    subcommand, args = rest[0], rest[1:]

    if subcommand == "push":
        options = tokens[1 : len(tokens) - len(rest)]
        return _judge_push(args, _repository(options, cwd))
    if subcommand == "checkout":
        return _judge_checkout(args)
    if subcommand == "restore":
        return _judge_restore(args)
    if subcommand == "reset" and "--hard" in args:
        return deny("git reset --hard discards uncommitted work.")
    if subcommand == "stash" and args and args[0] in ("drop", "clear"):
        return deny(f"git stash {args[0]} discards stashed work.")
    if subcommand == "clean" and any(_FORCE_SHORT.match(a) for a in args):
        return deny("git clean -f deletes files that are not committed.")
    return allow()


def _judge_push(args: list[str], repository: str | None) -> Decision:
    if "--force" in args or any(_FORCE_SHORT.match(a) for a in args):
        return deny("git push --force rewrites history. Push without it.")
    if "--tags" in args or "--follow-tags" in args:
        return deny(
            "Pushing tags publishes to PyPI. "
            f"Push one release tag alone: git push {RELEASE_REMOTE} vX.Y.Z"
        )

    positional = [a for a in args if not a.startswith("-")]
    for target in positional[1:]:
        # A refspec is ``src:dst``. The destination is what is written.
        destination = target.rsplit(":", 1)[-1]
        if destination in PROTECTED_BRANCHES:
            return deny(
                f"Pushing to {destination} is not allowed. Push a branch and open a PR."
            )
        if _TAG_LIKE.match(destination):
            return _judge_tag_push(positional, repository)

    if any(a.startswith("--force-with-lease") for a in args):
        return ask("a lease push still rewrites the branch on the remote")
    return allow()


def _judge_tag_push(positional: list[str], repository: str | None) -> Decision:
    """Allow a push of one merged release tag. Deny every other tag push."""
    if len(positional) != 2 or positional[0] != RELEASE_REMOTE:
        return deny(
            "Pushing a tag publishes to PyPI. "
            f"Push one release tag alone: git push {RELEASE_REMOTE} vX.Y.Z"
        )
    found = _RELEASE_TAG.match(positional[1])
    if found is None:
        return deny(f"{positional[1]} is not a release tag vX.Y.Z.")
    tag, version = found.groups()
    if repository is None:
        return deny("Cannot tell which repository the tag push reads. Drop the cd.")

    commit = _git(repository, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
    if commit is None:
        return deny(f"Cannot find the tag {tag} in {repository}.")
    if _git(repository, "merge-base", "--is-ancestor", commit, RELEASED_FROM) is None:
        return deny(
            f"{tag} is not on {RELEASED_FROM}. Merge the release pull request, "
            "then fetch origin main."
        )
    pyproject = _git(repository, "show", f"{commit}:pyproject.toml")
    declared = _VERSION_LINE.search(pyproject or "")
    if declared is None or declared.group(1) != version:
        return deny(f"pyproject.toml at {tag} does not declare version {version}.")
    return allow()


def _git(repository: str, *args: str) -> str | None:
    """Return what git prints, stripped, or None when it fails or cannot run."""
    try:
        result = subprocess.run(
            ["git", "-C", repository, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _repository(options: list[str], cwd: str | None) -> str | None:
    """Return the directory git runs in: *cwd*, moved by each ``-C``."""
    if cwd is None:
        return None
    where = cwd
    for index, option in enumerate(options[:-1]):
        if option == "-C":
            where = os.path.join(where, options[index + 1])
    return where


def _judge_checkout(args: list[str]) -> Decision:
    if "--" in args or "." in args:
        return deny(
            "git checkout -- <path> discards uncommitted work. "
            "Use git stash to keep it, or commit first."
        )
    return allow()


def _judge_restore(args: list[str]) -> Decision:
    staged = "--staged" in args or "-S" in args
    worktree = "--worktree" in args or "-W" in args
    if staged and not worktree:
        return allow()
    return deny(
        "git restore <path> discards uncommitted work. "
        "Use git stash to keep it, or commit first."
    )


def _after_git_options(tokens: list[str]) -> list[str]:
    """Return the tokens from the subcommand on."""
    index = 0
    takes_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
    while index < len(tokens) and tokens[index].startswith("-"):
        step = 2 if tokens[index] in takes_value else 1
        index += step
    return tokens[index:]


def _without_redirections(tokens: list[str]) -> list[str]:
    """Return the words of a command without its redirections and their targets."""
    words: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
        elif _REDIRECTION_ALONE.match(token):
            skip = True
        elif not _REDIRECTION.match(token):
            words.append(token)
    return words


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        # An unbalanced quote. Judge the words as they stand.
        return segment.split()


def main() -> int:
    data = read_input()
    if data is None or data.get("tool_name") != "Bash":
        return 0
    command = data.get("tool_input", {}).get("command", "")
    if not isinstance(command, str):
        return 0
    cwd = data.get("cwd")
    return answer(judge(command, cwd if isinstance(cwd, str) else None), HOOK)


if __name__ == "__main__":
    sys.exit(main())
