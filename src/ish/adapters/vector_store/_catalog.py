"""Find the stored indexes, and say which tree each one describes.

Every scanned tree has one index file in one directory. The file is
named after the tree, with a digest of its full path, so two projects
never share an index and a person reading the directory can still tell
them apart. The tree itself is recorded inside the file, because the
name carries only a hash of it, and so are the repository and the
checkout the tree belongs to, when git could name them.
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ish.adapters.vector_store.sqlite import (
    MATRIX_SUFFIX,
    SHARED_PREFIX,
    SqliteVectorStore,
)

log = logging.getLogger(__name__)

# Trees already reported as gone, so a server that reads the catalog
# every half minute says it once.
_REPORTED_GONE: set[Path] = set()


def _digest(path: Path) -> str:
    """Return the short digest of *path* that names a file after it."""
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]


def _gone(tree: Path) -> bool:
    """Return True when *tree* is no longer there.

    A permission error means "cannot tell" and keeps the index. Absence
    has many causes, and an index costs hours to build again.
    """
    try:
        os.stat(tree)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One stored index: the tree it describes, and where that tree belongs.

    ``present`` says whether the tree is still there. An index outlives
    a worktree made for one branch and deleted with it, and a search
    must not answer from it, nor a preview try to open its files.
    """

    tree: Path
    path: Path
    repository: Path | None = None
    checkout: Path | None = None
    present: bool = True


class IndexCatalog:
    """Answer which index files exist under one directory, and for which trees."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, root: Path) -> Path:
        """Return the index file for the tree at *root*."""
        resolved = root.resolve()
        return self.directory / f"{resolved.name}-{_digest(resolved)}.db"

    def shared_store_for(self, repository: Path) -> Path:
        """Return the file that holds the vectors of every tree in *repository*.

        Name it after the repository's common directory, which every
        worktree reports alike, so every checkout reaches the same file.
        """
        return self.directory / f"{SHARED_PREFIX}{_digest(repository.resolve())}.db"

    def below(self, path: Path) -> dict[Path, Path]:
        """Return every stored index whose tree sits at or below *path*.

        Map each tree to its index file. Read the tree from inside each
        index, because the file name carries only a hash of it.
        """
        return {entry.tree: entry.path for entry in self.entries_below(path)}

    def entries_below(self, path: Path) -> list[IndexEntry]:
        """Return every stored index whose tree is present at or below *path*."""
        wanted = path.resolve()
        return [
            entry
            for entry in self.entries()
            if entry.present and (entry.tree == wanted or wanted in entry.tree.parents)
        ]

    def covering(
        self, path: Path, checkout: Path | None = None
    ) -> tuple[Path, Path] | None:
        """Return the nearest stored index whose tree contains *path*.

        Asking about a directory inside an indexed tree should read what
        is already there, rather than building a second index for it.
        Prefer the closest ancestor, which describes the path best.

        A worktree kept under the main checkout lies inside its tree and
        is another checkout all the same. Name the *checkout* of *path*,
        and an index of another checkout does not cover it.
        """
        wanted = path.resolve()
        best: IndexEntry | None = None
        for entry in self.entries():
            if not entry.present or entry.tree not in wanted.parents:
                continue
            if (
                checkout is not None
                and entry.checkout is not None
                and entry.checkout != checkout
            ):
                continue
            if best is None or len(entry.tree.parts) > len(best.tree.parts):
                best = entry
        return None if best is None else (best.tree, best.path)

    def entries(self) -> list[IndexEntry]:
        """Return every index file that records its tree, with what it records.

        Include an index whose tree is gone, marked so, and say so once.
        A search leaves it out. A sweep of the shared store still counts
        what it references, because its vectors would otherwise be lost
        while its rows still name them.
        """
        if not self.directory.is_dir():
            return []
        found: list[IndexEntry] = []
        for db_path in sorted(self.directory.glob("*.db")):
            identity = SqliteVectorStore.read_identity(db_path)
            tree = identity.get("root")
            if tree is None:
                continue
            present = not _gone(tree)
            if not present and tree not in _REPORTED_GONE:
                _REPORTED_GONE.add(tree)
                log.info(
                    "The tree %s is gone. Its index is left out of every search. "
                    "Remove it with: ish --forget %s",
                    tree,
                    tree,
                )
            found.append(
                IndexEntry(
                    tree=tree,
                    path=db_path,
                    repository=identity.get("repository"),
                    checkout=identity.get("checkout"),
                    present=present,
                )
            )
        return found

    def forget(self, entry: IndexEntry) -> list[Path]:
        """Delete the index file of *entry* and its companions. Return what went."""
        gone: list[Path] = []
        for suffix in ("", "-wal", "-shm", MATRIX_SUFFIX):
            companion = entry.path.with_name(entry.path.name + suffix)
            if companion.exists():
                companion.unlink()
                gone.append(companion)
        return gone
