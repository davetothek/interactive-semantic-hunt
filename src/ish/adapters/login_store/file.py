"""Keep the logins of a server in a file of JSON lines, one login a line.

Each line holds the SHA-256 of a token, its user, its method, and when
it expires. The file never holds a token. The operator ends a login by
deleting its line, and the server reads the file again when it changes,
so the next request with that token is refused.
"""

import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ish.application.ports.login_store import Login

log = logging.getLogger(__name__)

# What a reader of the directory may do: nothing. The file is the user's.
FILE_MODE = 0o600


class LoginFile:
    """Keep the logins of a server in one file that only its user can read."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        """Keep the logins in *path*. Create the file at the first login."""
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._stamp: tuple[int, int, int] | None = None
        self._logins: dict[str, Login] = {}

    def add(self, login: Login) -> None:
        """Keep *login*, drop each login that has expired, and write the file."""
        with self._lock:
            self._reload()
            now = self._clock()
            current = [old for old in self._logins.values() if old.expires > now]
            current.append(login)
            self._write(current)
            self._logins = {each.digest: each for each in current}
            self._stamp = self._stamp_of()

    def find(self, digest: str) -> Login | None:
        """Return the login with *digest*, from the file as it is now."""
        with self._lock:
            self._reload()
            return self._logins.get(digest)

    def _stamp_of(self) -> tuple[int, int, int] | None:
        try:
            status = self._path.stat()
        except FileNotFoundError:
            return None
        return (status.st_ino, status.st_mtime_ns, status.st_size)

    def _reload(self) -> None:
        """Read the file again when it changed since the last read.

        A file that cannot be read admits nobody, and is read again at
        the next request. The logins read before it never stand in for it.
        """
        stamp = self._stamp_of()
        if stamp == self._stamp:
            return
        try:
            self._logins = {} if stamp is None else self._read()
        except (OSError, UnicodeDecodeError) as exc:
            log.error(
                "Cannot read %s, so it admits nobody: %s",
                self._path,
                type(exc).__name__,
            )
            self._logins = {}
            self._stamp = None
            return
        self._stamp = stamp

    def _read(self) -> dict[str, Login]:
        logins: dict[str, Login] = {}
        text = self._path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                login = Login(
                    str(entry["digest"]),
                    str(entry["user"]),
                    str(entry["method"]),
                    float(entry["expires"]),
                )
                if not math.isfinite(login.expires):
                    raise ValueError("a login expires at a moment in time")
            except (ValueError, KeyError, TypeError):
                # A line that the operator broke admits nobody.
                log.warning(
                    "Skip line %d of %s: it is not a login.", number, self._path
                )
                continue
            logins[login.digest] = login
        return logins

    def _write(self, logins: list[Login]) -> None:
        """Write *logins* to a new file, then put it in place in one step."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(
            json.dumps(
                {
                    "digest": login.digest,
                    "user": login.user,
                    "method": login.method,
                    "expires": login.expires,
                }
            )
            + "\n"
            for login in logins
        )
        part = self._path.with_name(f".{self._path.name}.{os.getpid()}.part")
        part.unlink(missing_ok=True)
        descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(lines)
        os.replace(part, self._path)
