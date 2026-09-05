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
import datetime
import re
import subprocess
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
CHANGELOG = Path("CHANGELOG.md")
VERSION_LINE = re.compile(r'(?m)^version = "(.*)"$')
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
UNRELEASED = "## Unreleased"


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


def section(text: str, heading: str) -> str:
    """Return what stands under *heading*, up to the next one.

    Match the heading and its version alone, so `## 0.1.1 - 2026-09-05`
    answers to `## 0.1.1` and `## 0.1.10 - ...` does not. The rest of the
    heading line must start with a space, which is what keeps one version
    from answering for a longer one that begins with it.
    """
    wanted = re.compile(
        rf"(?m)^{re.escape(heading)}(?: [^\n]*)?\n(.*?)(?=^## |\Z)", re.DOTALL
    )
    found = wanted.search(text)
    return found.group(1).strip() if found else ""


def promote(text: str, version: str, today: str) -> str:
    """Return *text* with the Unreleased entries named for *version*.

    Leave an empty Unreleased heading above, so the next change has
    somewhere to go without anyone making the heading first.
    """
    if UNRELEASED not in text:
        raise ReleaseError(f"CHANGELOG.md has no {UNRELEASED} heading")
    if not section(text, UNRELEASED):
        raise ReleaseError(
            f"CHANGELOG.md has nothing under {UNRELEASED}; "
            "a release says what it changed"
        )
    return text.replace(UNRELEASED, f"{UNRELEASED}\n\n## {version} - {today}", 1)


def today() -> str:
    """Return today's date, as a changelog heading writes it."""
    return datetime.date.today().isoformat()


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

    if not CHANGELOG.exists():
        raise ReleaseError("CHANGELOG.md is missing, so this release says nothing")
    notes = CHANGELOG.read_text(encoding="utf-8")

    if bumping:
        print(f"Releasing {current} -> {version}\n")
        # Name the entries before writing the version, so a changelog with
        # nothing under Unreleased stops the release with the tree untouched.
        CHANGELOG.write_text(promote(notes, version, today()), encoding="utf-8")
        PYPROJECT.write_text(set_version(text, version), encoding="utf-8")
    else:
        if not section(notes, f"## {version}"):
            raise ReleaseError(f"CHANGELOG.md has no entry for {version}")
        print(f"{version} is declared already, so proving it and tagging it\n")

    try:
        lock()
        check()
    except ReleaseError:
        # Put back only what this script just wrote. The tree was clean
        # above, so there is nothing else here to lose.
        git("checkout", "--", str(PYPROJECT), "uv.lock", str(CHANGELOG))
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
    parser.add_argument(
        "--notes",
        metavar="VERSION",
        help="Print this version's changelog entry and stop. The release "
        "workflow takes a release body from it, so the page and the file "
        "cannot disagree.",
    )
    arguments = parser.parse_args(argv)

    if arguments.notes:
        wanted = arguments.notes.removeprefix("v")
        found = section(CHANGELOG.read_text(encoding="utf-8"), f"## {wanted}")
        if not found:
            print(f"release: CHANGELOG.md has no entry for {wanted}", file=sys.stderr)
            return 1
        print(found)
        return 0

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
