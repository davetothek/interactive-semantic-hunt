"""Authenticator and sign-in protocol definitions.

An authenticator decides whether a request to a server over HTTP comes
from someone the server admits. A server asks each method it runs in
turn, so a static token and a login token can both open it, and the
transport knows no method.

A sign-in method checks a user name and a password once, such as against
a directory. The server then gives a login token for a time, and an
authenticator admits that token.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Identity:
    """Who sent a request, and the method that admitted it."""

    user: str
    method: str


@runtime_checkable
class Authenticator(Protocol):
    """Contract for one way a server admits a request."""

    def admit(self, bearer: str) -> Identity | None:
        """Return who carries the bearer token *bearer*.

        Return None when this method does not know the token, so the
        server can ask the next method.
        """
        ...


class SignInRefused(Exception):
    """Report a user name and a password that a sign-in method refuses.

    The text names neither the password nor which of the two is wrong.
    """


class SignInUnavailable(Exception):
    """Report a sign-in method that cannot check a password now.

    A directory that cannot be reached is one. The text says so, so a
    user does not take it for a wrong password.
    """


class SignInLimited(Exception):
    """Report a user name that failed to sign in too often to try again now.

    The server asks the method nothing while the limit holds.
    """


@runtime_checkable
class SignIn(Protocol):
    """Contract for one way a user signs in with a name and a password."""

    def sign_in(self, user: str, password: str) -> Identity:
        """Return who signed in.

        Raise ``SignInRefused`` for a name or a password the method
        refuses, and ``SignInUnavailable`` when it cannot check them.
        """
        ...
