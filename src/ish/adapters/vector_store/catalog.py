"""Find the stored indexes, and say which tree each one describes.

Every scanned tree has one index file in one directory. The file is
named after the tree, with a digest of its full path, so two projects
never share an index and a person reading the directory can still tell
them apart. The tree itself is recorded inside the file, because the
name carries only a hash of it, and so are the repository and the
checkout the tree belongs to, when git could name them.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ish.adapters.vector_store.sqlite import SHARED_PREFIX, SqliteVectorStore


def _digest(path: Path) -> str:
    """Return the short digest of *path* that names a file after it."""
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One stored index: the tree it describes, and where that tree belongs."""

    tree: Path
    path: Path
    repository: Path | None = None
    checkout: Path | None = None


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
        """Return every stored index whose tree sits at or below *path*."""
        wanted = path.resolve()
        return [
            entry
            for entry in self.entries()
            if entry.tree == wanted or wanted in entry.tree.parents
        ]

    def covering(self, path: Path) -> tuple[Path, Path] | None:
        """Return the nearest stored index whose tree contains *path*.

        Asking about a directory inside an indexed tree should read what
        is already there. Building a second index for it would embed
        every file again, because a vector is shared only within one
        index file. Prefer the closest ancestor, which describes the
        path best.
        """
        wanted = path.resolve()
        best: IndexEntry | None = None
        for entry in self.entries():
            if entry.tree not in wanted.parents:
                continue
            if best is None or len(entry.tree.parts) > len(best.tree.parts):
                best = entry
        return None if best is None else (best.tree, best.path)

    def entries(self) -> list[IndexEntry]:
        """Return every index file that records its tree, with what it records."""
        if not self.directory.is_dir():
            return []
        found: list[IndexEntry] = []
        for db_path in sorted(self.directory.glob("*.db")):
            identity = SqliteVectorStore.read_identity(db_path)
            tree = identity.get("root")
            if tree is None:
                continue
            found.append(
                IndexEntry(
                    tree=tree,
                    path=db_path,
                    repository=identity.get("repository"),
                    checkout=identity.get("checkout"),
                )
            )
        return found
