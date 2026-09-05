"""Cut a release: set the version, prove it builds, commit, and tag.

    poe release          # raise the patch number
    poe release 0.2.0    # release exactly this version

Naming the version pyproject.toml already declares tags that version
rather than raising it, because a release prepared and never tagged is
a release still owed.

Leave the tag unpushed. Pushing it is the decision to publish, and
`release.yml` takes over from there.

Give the version when a milestone names it, and omit it for a fix that
cannot wait for one. A patch belongs to the release it fixes, so cut it
from that tag rather than from main:

    git switch -c hotfix/0.1.2 v0.1.1
    git cherry-pick <the fix already on main>
    poe release
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
VERSION_LINE = re.compile(r'(?m)^version = "(.*)"$')
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class ReleaseError(Exception):
    """Raise when a release must not go ahead."""


def read_version(text: str) -> str:
    """Return the version pyproject.toml declares."""
    found = VERSION_LINE.search(text)
    if not found:
        raise ReleaseError("pyproject.toml declares no version")
    return found.group(1)


def set_version(text: str, version: str) -> str:
    """Return *text* with its first version line replaced.

    Replace one line only. A dependency further down the file may name a
    version of its own, and it is not this one.
    """
    new_text, count = VERSION_LINE.subn(f'version = "{version}"', text, count=1)
    if count != 1:
        raise ReleaseError("pyproject.toml declares no version")
    return new_text


def next_patch(version: str) -> str:
    """Return *version* with its patch number raised by one."""
    if not SEMVER.match(version):
        raise ReleaseError(f"{version!r} is not a plain X.Y.Z, so nothing to raise")
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def is_after(version: str, other: str) -> bool:
    """Return whether *version* stands later than *other*."""
    return tuple(int(part) for part in version.split(".")) > tuple(
        int(part) for part in other.split(".")
    )


def git(*arguments: str) -> str:
    """Run one git command and return what it said."""
    done = subprocess.run(
        ("git", *arguments), capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise ReleaseError(f"git {' '.join(arguments)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def check() -> None:
    """Run the project's own checks, showing what they say."""
    if subprocess.run(("uv", "run", "poe", "check"), check=False).returncode != 0:
        raise ReleaseError("poe check failed, so nothing was committed")


def lock() -> None:
    """Bring uv.lock in step with the version just written."""
    if subprocess.run(("uv", "lock"), check=False).returncode != 0:
        raise ReleaseError("uv lock failed, so nothing was committed")


def cut(asked: str | None) -> str:
    """Set the version, prove it, commit it, and tag it. Return the version."""
    if git("status", "--porcelain"):
        raise ReleaseError("the working tree has changes; commit or stash them first")

    text = PYPROJECT.read_text(encoding="utf-8")
    current = read_version(text)
    version = asked.removeprefix("v") if asked else next_patch(current)
    if not SEMVER.match(version):
        raise ReleaseError(f"{version!r} is not a plain X.Y.Z")
    if git("tag", "--list", f"v{version}"):
        raise ReleaseError(f"v{version} exists already")

    # A version already declared is a release that was prepared and never
    # tagged. Prove it and tag it: there is nothing left to write.
    bumping = version != current
    if bumping and not is_after(version, current):
        raise ReleaseError(f"{version} does not come after the declared {current}")

    if bumping:
        print(f"Releasing {current} -> {version}\n")
        PYPROJECT.write_text(set_version(text, version), encoding="utf-8")
    else:
        print(f"{version} is declared already, so proving it and tagging it\n")

    try:
        lock()
        check()
    except ReleaseError:
        # Put back only what this script just wrote. The tree was clean
        # above, so there is nothing else here to lose.
        git("checkout", "--", str(PYPROJECT), "uv.lock")
        raise

    # Commit whatever changed. A version already declared leaves nothing to
    # commit, unless relocking moved the lock file.
    if git("status", "--porcelain"):
        git("commit", "-am", f"Release {version}")
    git("tag", f"v{version}")
    return version


def main(argv: list[str] | None = None) -> int:
    """Cut a release. Return an exit code."""
    parser = argparse.ArgumentParser(
        prog="poe release",
        description="Set the version, prove it builds, commit, and tag.",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="Version to release. Omit it to raise the patch number.",
    )
    arguments = parser.parse_args(argv)

    try:
        version = cut(arguments.version)
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1

    branch = git("branch", "--show-current")
    print(f"\nTagged v{version}. Publish it with:\n")
    print(f"    git push origin {branch} v{version}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
