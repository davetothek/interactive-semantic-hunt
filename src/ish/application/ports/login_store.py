"""Login store protocol definition.

Satisfied by adapters that keep the login tokens a server gave. A store
keeps the SHA-256 of each token and never the token, so a copy of the
store opens nothing.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Login:
    """One login token as the server keeps it."""

    digest: str
    user: str
    method: str
    expires: float


@runtime_checkable
class LoginStore(Protocol):
    """Contract for keeping the login tokens of a server."""

    def add(self, login: Login) -> None:
        """Keep *login*, and drop each login that has expired."""
        ...

    def find(self, digest: str) -> Login | None:
        """Return the login with the SHA-256 *digest*, or None."""
        ...
