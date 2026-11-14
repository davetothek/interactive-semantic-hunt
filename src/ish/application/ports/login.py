"""Login protocol definition.

Satisfied by adapters that sign this machine in to one server, and out
again. A sign-in sends a user name and a password once, and keeps the
login token the server gives. The password is never kept.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class LoginError(Exception):
    """Report a sign-in that did not give a login token. No password is in the text."""


@dataclass(frozen=True, slots=True)
class SignedIn:
    """Who a login token stands for, and when it expires."""

    user: str
    expires: float


@runtime_checkable
class Login(Protocol):
    """Contract for signing in to one server, and out again."""

    @property
    def host(self) -> str:
        """Return the name of the server, for a line that says where."""
        ...

    def methods(self) -> list[str]:
        """Return the sign-in methods the server offers. Send no password."""
        ...

    def password_problem(self) -> str:
        """Return why no password may go to this server, or an empty text."""
        ...

    def sign_in(self, method: str, user: str, password: str) -> SignedIn:
        """Sign in with *method*, keep the login token, and return who it is for.

        Raise ``LoginError`` when the server gives no token.
        """
        ...

    def sign_out(self) -> bool:
        """Delete the login token of the server. Return False when none was kept."""
        ...
