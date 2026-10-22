"""Tell the clients of a tree that ``ish-serve`` keeps its index current.

``ish-serve`` fills the index. Every client reads the index itself, so
nothing connects the two. A client must know one thing: whether a
server is alive for the tree it asks about. A client that finds none
brings the index up to date on its own. The server says so through a
status file under the runtime directory of the user. It writes the
file every few seconds while it runs, and the file names what the
server is doing. A file nobody has written for ``STALE_SECONDS`` is a
dead server, and a client reads past it.

A client asks for a refresh now by touching a nudge file beside the
status file. The server looks for it once a second.
"""

import contextlib
import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from ish.runtime import runtime_dir

STATUS_SUFFIX = ".status"
NUDGE_SUFFIX = ".refresh"

# How many characters of the name of the tree the file name keeps.
NAME_CHARS = 40

# How often a server writes its status while nothing changes.
BEAT_SECONDS = 5.0

# How old a status may be and still mean a live server. Six beats, so
# one slow write does not read as a death.
STALE_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class Status:
    """What a server last said about the tree it keeps current.

    *updated* is the wall clock of the write. *refreshing* names the
    step a refresh is at, or None between refreshes. *repository* and
    *checkout* are what git says of the tree, so a client can tell
    whether the refresh of the server reaches its own path.
    """

    tree: Path
    pid: int
    updated: float
    repository: Path | None = None
    checkout: Path | None = None
    refresh_seconds: float = 0.0
    refreshing: str | None = None
    last_finished: float | None = None

    def fresh(self, now: float | None = None) -> bool:
        """Return True while the server that wrote this counts as alive."""
        at = time.time() if now is None else now
        return at - self.updated < STALE_SECONDS

    def covers(self, path: Path, repository: Path | None) -> bool:
        """Return True when the refresh of the server reaches *path*.

        The server refreshes every index at or below its tree, and every
        index of its own repository in any other checkout, wherever that
        checkout is. A path in another repository is reached only under
        the tree of the server. *repository* is what git says of *path*.
        """
        if repository is not None and repository == self.repository:
            return True
        return path == self.tree or self.tree in path.parents


def _named(root: Path, suffix: str, environ: Mapping[str, str] | None) -> Path:
    """Return the file of *root* with *suffix*, named the way its index is.

    The name of the tree, then a digest of its full path, so two trees
    of one name stay apart.
    """
    resolved = root.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    name = resolved.name[:NAME_CHARS] or "root"
    return runtime_dir(environ) / f"{name}-{digest}{suffix}"


def status_for(root: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Return where the server of *root* writes its status."""
    return _named(root, STATUS_SUFFIX, environ)


def nudge_for(root: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Return the file a client touches to ask the server of *root* for a refresh."""
    return _named(root, NUDGE_SUFFIX, environ)


def write(path: Path, status: Status) -> None:
    """Write *status* at *path* in one step, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    plain = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in asdict(status).items()
    }
    partial = path.with_name(f"{path.name}.{status.pid}.tmp")
    partial.write_text(json.dumps(plain), encoding="utf-8")
    os.replace(partial, path)


def read(path: Path) -> Status | None:
    """Return the status at *path*, or None when there is none to read.

    A file that is missing, half written, or of another shape is no
    status. The caller then acts as if no server ran.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        refreshing = raw.get("refreshing")
        finished = raw.get("last_finished")
        return Status(
            tree=Path(raw["tree"]),
            pid=int(raw["pid"]),
            updated=float(raw["updated"]),
            repository=_path_or_none(raw.get("repository")),
            checkout=_path_or_none(raw.get("checkout")),
            refresh_seconds=float(raw.get("refresh_seconds", 0.0)),
            refreshing=None if refreshing is None else str(refreshing),
            last_finished=None if finished is None else float(finished),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _path_or_none(value: object) -> Path | None:
    return None if value is None else Path(str(value))


def statuses(environ: Mapping[str, str] | None = None) -> list[Status]:
    """Return every status written under the runtime directory, stale ones included.

    There is one file per server, and a few servers at most, so reading
    them all costs less than asking git which one to read.
    """
    directory = runtime_dir(environ)
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob(f"*{STATUS_SUFFIX}")):
        status = read(path)
        if status is not None:
            found.append(status)
    return found


def find(
    path: Path,
    repository: Path | None = None,
    environ: Mapping[str, str] | None = None,
    now: float | None = None,
) -> Status | None:
    """Return the status of the live server whose refresh reaches *path*.

    A server of the repository *path* belongs to reaches it from any
    checkout, and a server of a tree above *path* reaches it too. When
    several do, take the one of the nearest tree. *repository* is what
    git says of *path*.
    """
    resolved = path.resolve()
    best: Status | None = None
    for status in statuses(environ):
        if not status.fresh(now) or not status.covers(resolved, repository):
            continue
        if best is None or len(status.tree.parts) > len(best.tree.parts):
            best = status
    return best


def nudge(root: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Ask the server of *root* to refresh now. Return the file touched."""
    path = nudge_for(root, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def take_nudge(path: Path) -> bool:
    """Take the nudge at *path* away. Return True when there was one."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def remove(path: Path) -> None:
    """Take the status at *path* away, when there is one."""
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def alive(pid: int) -> bool:
    """Return True when a process with *pid* runs."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
