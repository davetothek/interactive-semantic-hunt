"""Ask git which files it ignores.

Indexing what a repository deliberately ignores fills the index with
build output and vendored code. Git already knows the answer, including
every nested ``.gitignore`` and the user's global excludes, so ask it
rather than reimplementing the rules.
"""

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


class GitVisibleFiles:
    """Report whether git would show a file.

    Run one command per repository and cache the answer, so the question
    stays cheap enough for the scan to ask about every candidate.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._visible: set[Path] | None = None
        self._repo: Path | None = None

    def ignores(self, path: Path) -> bool:
        """Return True when git would not show *path*."""
        visible = self._load()
        if visible is None:
            # Not a repository, or git is unavailable. Ignore nothing.
            return False
        if self._repo is not None and self._repo not in path.parents:
            # Outside the repository, so git has no opinion.
            return False
        return path not in visible

    def _load(self) -> set[Path] | None:
        """Read the visible file set once."""
        if self._visible is not None:
            return self._visible

        top = self._run(["rev-parse", "--show-toplevel"])
        if top is None:
            return None
        self._repo = Path(top.strip()).resolve()

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
