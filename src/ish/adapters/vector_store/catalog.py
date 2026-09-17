"""Find the stored indexes, and say which tree each one describes.

Every scanned tree has one index file in one directory. The file is
named after the tree, with a digest of its full path, so two projects
never share an index and a person reading the directory can still tell
them apart. The tree itself is recorded inside the file, because the
name carries only a hash of it.
"""

import hashlib
from pathlib import Path

from ish.adapters.vector_store.sqlite import SqliteVectorStore


class IndexCatalog:
    """Answer which index files exist under one directory, and for which trees."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, root: Path) -> Path:
        """Return the index file for the tree at *root*."""
        resolved = root.resolve()
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        return self.directory / f"{resolved.name}-{digest}.db"

    def below(self, path: Path) -> dict[Path, Path]:
        """Return every stored index whose tree sits at or below *path*.

        Map each tree to its index file. Read the tree from inside each
        index, because the file name carries only a hash of it.
        """
        wanted = path.resolve()
        found: dict[Path, Path] = {}
        for tree, db_path in self._trees():
            if tree == wanted or wanted in tree.parents:
                found[tree] = db_path
        return found

    def covering(self, path: Path) -> tuple[Path, Path] | None:
        """Return the nearest stored index whose tree contains *path*.

        Asking about a directory inside an indexed tree should read what
        is already there. Building a second index for it would embed
        every file again, because a vector is shared only within one
        index file. Prefer the closest ancestor, which describes the
        path best.
        """
        wanted = path.resolve()
        best: tuple[Path, Path] | None = None
        for tree, db_path in self._trees():
            if tree not in wanted.parents:
                continue
            if best is None or len(tree.parts) > len(best[0].parts):
                best = (tree, db_path)
        return best

    def _trees(self) -> list[tuple[Path, Path]]:
        """Return every index file that records its tree, with that tree."""
        if not self.directory.is_dir():
            return []
        found: list[tuple[Path, Path]] = []
        for db_path in sorted(self.directory.glob("*.db")):
            tree = SqliteVectorStore.read_root(db_path)
            if tree is not None:
                found.append((tree, db_path))
        return found
