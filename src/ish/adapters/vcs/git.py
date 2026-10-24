"""Ask git which files it ignores, and which repository a tree belongs to.

Indexing what a repository deliberately ignores fills the index with
build output and vendored code. Git already knows the answer, including
every nested ``.gitignore`` and the user's global excludes, so ask it
rather than reimplementing the rules.

Git also knows which checkouts are one repository. Every worktree
reports the same common directory, which is what lets two checkouts of
one project share the work of embedding.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class Repository:
    """Name the repository a tree belongs to, and the checkout it is in.

    Every worktree of one repository reports the same ``common_dir``,
    where the objects live. Each reports its own ``checkout``, the top
    of the working tree it belongs to.
    """

    common_dir: Path
    checkout: Path


def repository_of(root: Path) -> Repository | None:
    """Return the repository *root* belongs to, or None.

    None means the path is in no repository, or git cannot run. A
    caller then treats the tree as a repository of its own.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--git-common-dir",
                "--show-toplevel",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("git is unavailable: %s", exc)
        return None
    if result.returncode != 0:
        log.debug("git rev-parse failed: %s", result.stderr.strip())
        return None
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return None
    # The main checkout reports its common directory as a relative
    # path, so resolve it against the directory git was asked in.
    common = Path(lines[0])
    if not common.is_absolute():
        common = root / common
    return Repository(common_dir=common.resolve(), checkout=Path(lines[1]).resolve())


class GitVisibleFiles:
    """Report whether git would show a file.

    Run one command per repository and cache the answer, so the question
    stays cheap enough for the scan to ask about every candidate.
    """

    def __init__(self, root: Path, checkout: Path | None = None) -> None:
        """Filter the files under *root*.

        *checkout* is the top of the working tree, when the caller has
        already asked git for it. Without it, the filter asks.
        """
        self._root = root
        self._visible: set[Path] | None = None
        self._repo: Path | None = checkout
        self._inside: str | None = None

    def ignores(self, path: Path) -> bool:
        """Return True when git would not show *path*."""
        visible = self._load()
        if visible is None:
            # Not a repository, or git is unavailable. Ignore nothing.
            return False
        if self._inside is not None and not os.path.normcase(str(path)).startswith(
            self._inside
        ):
            # Outside the repository, so git has no opinion.
            return False
        return path not in visible

    def _load(self) -> set[Path] | None:
        """Read the visible file set once."""
        if self._visible is not None:
            return self._visible

        if self._repo is None:
            top = self._run(["rev-parse", "--show-toplevel"])
            if top is None:
                return None
            self._repo = Path(top.strip()).resolve()
        # A path sits inside the repository when its text begins with
        # the repository's text and a separator. The scan asks about
        # every file, and the text answers without building a path per
        # level above the file.
        self._inside = os.path.normcase(str(self._repo)) + os.sep

        # Tracked files plus untracked files that no ignore rule covers.
        # --full-name reports every path from the repository root, so a
        # scan of a subdirectory still resolves against the same base.
        listing = self._run(
            [
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--full-name",
                "-z",
            ]
        )
        if listing is None:
            return None

        self._visible = {
            (self._repo / name).resolve() for name in listing.split("\0") if name
        }
        log.debug("git reports %d visible files in %s", len(self._visible), self._repo)
        return self._visible

    def _run(self, arguments: list[str]) -> str | None:
        """Run one git command, returning None when it cannot be used."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self._root), *arguments],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("git is unavailable: %s", exc)
            return None

        if result.returncode != 0:
            log.debug("git %s failed: %s", arguments[0], result.stderr.strip())
            return None
        return result.stdout
