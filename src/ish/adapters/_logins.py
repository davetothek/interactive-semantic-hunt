"""Keep the login tokens of this machine, one for each server.

``ish remote login`` writes a token here, and every command of ``ish
remote`` reads it when ``ISH_MCP_TOKEN`` is not set. The file holds a
token, its user, and when it expires, and never a password. Only the
user may read it: the file is created with mode 0600, and a file that
another user can read is refused.

The file is not encrypted. The key of an encryption would sit on the
same disk, so it would stop no reader that can read the file. The token
expires, opens one server only, and the operator can delete it there.
"""

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# Read and write for the user, and nothing for anyone else.
FILE_MODE = 0o600
DIRECTORY_MODE = 0o700


class TokenFileError(Exception):
    """Report a token file that ish refuses to use."""


@dataclass(frozen=True, slots=True)
class Saved:
    """The login token of one server, as this machine keeps it."""

    token: str
    user: str
    expires: float


def key_of(url: str) -> str:
    """Return the key of *url* in the file.

    Write the scheme and the host in lower case, and drop a slash at the
    end, so one server has one token however its URL is written.
    """
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            parts.query,
            "",
        )
    )


class TokenFile:
    """Keep one login token for each server URL in one file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, url: str) -> Saved | None:
        """Return the token of *url*, or None when none is kept.

        Raise ``TokenFileError`` when another user can read the file, or
        when it does not parse.
        """
        entry = self._read().get(key_of(url))
        if not isinstance(entry, dict):
            return None
        try:
            return Saved(
                str(entry["token"]), str(entry["user"]), float(entry["expires"])
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, url: str, saved: Saved) -> None:
        """Keep *saved* as the token of *url*, in place of one kept before."""
        entries = self._read()
        entries[key_of(url)] = {
            "token": saved.token,
            "user": saved.user,
            "expires": saved.expires,
        }
        self._write(entries)

    def remove(self, url: str) -> bool:
        """Delete the token of *url*. Return False when none was kept."""
        entries = self._read()
        key = key_of(url)
        if key not in entries:
            return False
        del entries[key]
        self._write(entries)
        return True

    def _read(self) -> dict[str, object]:
        try:
            status = self.path.stat()
        except FileNotFoundError:
            return {}
        if status.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise TokenFileError(
                f"Other users can read the token file {self.path}. "
                f"Run: chmod 600 {self.path}"
            )
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise TokenFileError(f"Cannot parse the token file {self.path}.") from exc
        if not isinstance(entries, dict):
            raise TokenFileError(f"Cannot parse the token file {self.path}.")
        return entries

    def _write(self, entries: dict[str, object]) -> None:
        """Write *entries* to a new file of mode 0600, then put it in place."""
        self.path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
        part = self.path.with_name(f".{self.path.name}.{os.getpid()}.part")
        part.unlink(missing_ok=True)
        descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(entries, stream, indent=1)
        os.replace(part, self.path)
