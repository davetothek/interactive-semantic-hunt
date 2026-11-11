"""Admit the requests of a server over HTTP, and sign users in.

A method such as the static token admits a bearer token directly. A
sign-in method checks a user name and a password once, and the server
then gives a login token that lasts for a time. The server keeps the
SHA-256 of each login token and never the token. A token of 32 random
bytes cannot be guessed from its digest, so the digest needs no key.
"""

import hashlib
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ish.application.ports.authenticator import Authenticator, Identity, SignIn
from ish.application.ports.login_store import Login, LoginStore

# The random bytes of a login token.
TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class Granted:
    """A login token, for the client that signed in. Only the client keeps it."""

    token: str
    user: str
    expires: float


def digest_of(token: str) -> str:
    """Return the SHA-256 of *token*, as the store keeps it."""
    return hashlib.sha256(token.encode()).hexdigest()


class Access:
    """Decide who a request comes from, and give login tokens."""

    def __init__(
        self,
        authenticators: Sequence[Authenticator],
        sign_ins: Mapping[str, SignIn],
        logins: LoginStore,
        lifetime: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Admit through *authenticators* and *logins*, and sign in through *sign_ins*.

        A login token lasts *lifetime* seconds.
        """
        self._authenticators = tuple(authenticators)
        self._sign_ins = dict(sign_ins)
        self._logins = logins
        self._lifetime = lifetime
        self._clock = clock

    def admit(self, bearer: str) -> Identity | None:
        """Return who carries *bearer*, or None to refuse the request.

        Ask each authenticator in turn, then the login tokens. A login
        token that has expired, or that the operator deleted, admits
        nobody.
        """
        if not bearer:
            return None
        for authenticator in self._authenticators:
            identity = authenticator.admit(bearer)
            if identity is not None:
                return identity
        login = self._logins.find(digest_of(bearer))
        if login is None or login.expires <= self._clock():
            return None
        return Identity(login.user, login.method)

    def methods(self) -> list[str]:
        """Return the names of the sign-in methods, in the order of ``auth``."""
        return list(self._sign_ins)

    def sign_in(self, method: str, user: str, password: str) -> Granted:
        """Check *user* and *password* with *method*, and give a login token.

        Raise ``ValueError`` for a method this server does not offer, and
        for an empty user name or password. Ask the method nothing then.
        Raise what the method raises when it refuses.
        """
        if method not in self._sign_ins:
            offered = ", ".join(self._sign_ins) or "none"
            raise ValueError(
                f"This server offers no sign-in method {method!r}. "
                f"It offers: {offered}."
            )
        if not user.strip():
            raise ValueError("Give a user name.")
        if not password:
            raise ValueError("Give a password.")
        identity = self._sign_ins[method].sign_in(user, password)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires = self._clock() + self._lifetime
        self._logins.add(
            Login(digest_of(token), identity.user, identity.method, expires)
        )
        return Granted(token, identity.user, expires)
