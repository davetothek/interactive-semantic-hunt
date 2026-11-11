"""Admit the requests of a server over HTTP, and sign users in.

A method such as the static token admits a bearer token directly. A
sign-in method checks a user name and a password once, and the server
then gives a login token that lasts for a time. The server keeps the
SHA-256 of each login token and never the token. A token of 32 random
bytes cannot be guessed from its digest, so the digest needs no key.
"""

import hashlib
import math
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ish.application.ports.authenticator import (
    Authenticator,
    Identity,
    SignIn,
    SignInLimited,
    SignInRefused,
)
from ish.application.ports.login_store import Login, LoginStore

# The random bytes of a login token.
TOKEN_BYTES = 32

# A user name that fails this many times in WINDOW_SECONDS is refused for
# WINDOW_SECONDS, and the method is not asked. A password guessed one at
# a time then costs hours, and the directory never sees the guesses.
MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60

# How often the record of failures drops what is older than the window.
PRUNE_SECONDS = 60


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
        # The recent failures of each user name, and when each limit ends.
        self._failures: dict[str, list[float]] = {}
        self._limited: dict[str, float] = {}
        # Sign-ins of each user name that the method still checks. Each
        # one counts against the limit, so parallel guesses do not pass it.
        self._checking: dict[str, int] = {}
        self._pruned = 0.0
        self._lock = threading.Lock()

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
        for an empty user name or password. Raise ``SignInLimited`` while a
        user name is over its limit of failures. Ask the method nothing
        then. Raise what the method raises when it refuses.
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
        key = user.strip().lower()
        now = self._clock()
        self._start(key, user.strip(), now)
        try:
            identity = self._sign_ins[method].sign_in(user, password)
        except SignInRefused:
            self._end(key, now, failed=True)
            raise
        except BaseException:
            self._end(key, now, failed=False)
            raise
        self._end(key, now, failed=False, cleared=True)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires = self._clock() + self._lifetime
        self._logins.add(
            Login(digest_of(token), identity.user, identity.method, expires)
        )
        return Granted(token, identity.user, expires)

    def _start(self, key: str, user: str, now: float) -> None:
        """Count a sign-in of *key* in flight, or raise ``SignInLimited``.

        A sign-in in flight counts as a failure until it ends, so many
        guesses sent at once reach the method no more often than one at
        a time would.
        """
        with self._lock:
            ends = self._limited.get(key, 0.0)
            if ends > now:
                minutes = math.ceil((ends - now) / 60)
                raise SignInLimited(
                    f"Too many failed sign-ins for {user}. "
                    f"Try again in {minutes} minutes."
                )
            failures = self._failures.get(key, [])
            recent = [t for t in failures if now - t < WINDOW_SECONDS]
            if len(recent) + self._checking.get(key, 0) >= MAX_FAILURES:
                raise SignInLimited(
                    f"Too many sign-ins for {user} at once. Try again in a minute."
                )
            self._checking[key] = self._checking.get(key, 0) + 1

    def _end(
        self, key: str, now: float, *, failed: bool, cleared: bool = False
    ) -> None:
        """End a sign-in of *key*, and count it when it *failed*."""
        with self._lock:
            left = self._checking.get(key, 1) - 1
            if left:
                self._checking[key] = left
            else:
                self._checking.pop(key, None)
            if cleared:
                self._failures.pop(key, None)
            if not failed:
                return
            if now - self._pruned >= PRUNE_SECONDS:
                self._prune(now)
            recent = [
                t for t in self._failures.get(key, []) if now - t < WINDOW_SECONDS
            ]
            recent.append(now)
            if len(recent) >= MAX_FAILURES:
                self._limited[key] = now + WINDOW_SECONDS
                self._failures.pop(key, None)
            else:
                self._failures[key] = recent

    def _prune(self, now: float) -> None:
        """Forget the failures and the limits that the window has passed."""
        self._pruned = now
        for name in [
            n for n, t in self._failures.items() if now - t[-1] >= WINDOW_SECONDS
        ]:
            del self._failures[name]
        for name in [n for n, ends in self._limited.items() if ends <= now]:
            del self._limited[name]
