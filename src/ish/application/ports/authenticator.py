"""Authenticator protocol definition.

Satisfied by adapters that decide whether a request to a server over HTTP
comes from someone the server admits. A server asks each method it runs
in turn, so a static token and a login token can both open it, and the
transport knows no method.
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
