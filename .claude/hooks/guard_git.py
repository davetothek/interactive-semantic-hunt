"""Stop a shell command that cannot be undone.

PreToolUse on Bash. Three groups of commands are judged:

- A push to the default branch, a push of a tag, and a forced push are
  denied. The first breaks the repository's own rule, the second
  publishes to PyPI, and the third rewrites history.
- A command that discards uncommitted work is denied. A model once
  reverted its own unfinished rewrite with ``git checkout -- file``.
- A command that adds a dependency asks first. The repository's ground
  rule says so.

A compound command is judged one segment at a time, so ``cd x && git
push origin main`` is still a push to main.
"""

import re
import shlex
import sys

from _common import Decision, allow, answer, ask, deny, read_input

HOOK = "guard_git"

PROTECTED_BRANCHES = frozenset({"main", "master"})

# A push target that names a tag. Tags in this repository are versions.
_TAG_LIKE = re.compile(r"^(refs/tags/|v\d)")

# A short option cluster that carries ``-f``, such as ``-f`` or ``-fu``.
_FORCE_SHORT = re.compile(r"^-[a-zA-Z]*f[a-zA-Z]*$")

# Where one shell command ends and the next starts.
_SEGMENT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")

# A leading ``NAME=value`` that sets an environment variable.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

DEPENDENCY_COMMANDS = (
    ("uv", "add"),
    ("uv", "remove"),
    ("uv", "pip"),
    ("pip", "install"),
    ("pip3", "install"),
    ("poetry", "add"),
)


def judge(command: str) -> Decision:
    """Return the decision for one Bash command line."""
    worst = allow()
    for segment in _SEGMENT.split(command):
        verdict = _judge_segment(segment)
        worst = _stricter(worst, verdict)
    return worst


def _stricter(first: Decision, second: Decision) -> Decision:
    """Return the decision that constrains more. Deny beats ask beats allow."""
    order = {"allow": 0, "ask": 1, "deny": 2}
    return second if order[second.kind] > order[first.kind] else first


def _judge_segment(segment: str) -> Decision:
    tokens = _tokens(segment)
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
        return _judge_push(args)
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


def _judge_push(args: list[str]) -> Decision:
    if "--force" in args or any(_FORCE_SHORT.match(a) for a in args):
        return deny("git push --force rewrites history. Push without it.")
    if "--tags" in args or "--follow-tags" in args:
        return deny("Pushing a tag publishes to PyPI. The user pushes tags.")

    positional = [a for a in args if not a.startswith("-")]
    for target in positional[1:]:
        # A refspec is ``src:dst``. The destination is what is written.
        destination = target.rsplit(":", 1)[-1]
        if destination in PROTECTED_BRANCHES:
            return deny(
                f"Pushing to {destination} is not allowed. Push a branch and open a PR."
            )
        if _TAG_LIKE.match(destination):
            return deny("Pushing a tag publishes to PyPI. The user pushes tags.")

    if any(a.startswith("--force-with-lease") for a in args):
        return ask("a lease push still rewrites the branch on the remote")
    return allow()


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
    return answer(judge(command), HOOK)


if __name__ == "__main__":
    sys.exit(main())
