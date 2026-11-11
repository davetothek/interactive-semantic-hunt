"""Every way a server over HTTP admits a request, and how to add one.

A method checks the bearer token of each request and says who carries
it, or checks the user name and the password of a sign-in. Write a class
with ``configured`` and one of ``admit`` or ``sign_in``:

    class MyMethod:
        @classmethod
        def configured(cls, options: Mapping[str, str]) -> "MyMethod":
            ...                              # read only the keys it needs

        def admit(self, bearer: str) -> Identity | None:
            ...                              # None for a token it does not know

        def sign_in(self, user: str, password: str) -> Identity:
            ...                              # raise SignInRefused for a wrong one

``configured`` gets the values ``bootstrap.build_access`` reads for
the methods, as strings. An adapter reads no settings and no environment
of its own. Raise ``ValueError`` when a value the method needs is
missing, so the server refuses to start and names it.

To add a method to ish:

1. Write the class in a new module in this package, beside the others.
2. Add one line to ``AUTHENTICATORS`` below: its ``configured``, keyed by
   its name in ``auth``.
3. Add the values it needs to ``bootstrap.build_access``.
"""

from collections.abc import Callable, Mapping

from ish.adapters.authenticator.token import StaticToken

AUTHENTICATORS: dict[str, Callable[[Mapping[str, str]], object]] = {
    "token": StaticToken.configured,
}
