"""Admit the one static token that the operator gives to every client."""

from collections.abc import Mapping

from ish.application.ports.authenticator import Identity

# The user a static token stands for. It names no person, because every
# client that holds the token sends the same one.
USER = "token"


class StaticToken:
    """Admit a request that carries the token of the operator."""

    def __init__(self, token: str, variable: str) -> None:
        """Admit *token* only. *variable* is where the operator sets it.

        Raise ``ValueError`` without a token. A server on a network with
        no token would answer anyone who can reach it.
        """
        if not token:
            raise ValueError(
                f"Set {variable} before serving MCP over HTTP. "
                "Every request must carry it as a bearer token."
            )
        self._token = token.encode()

    @classmethod
    def configured(cls, options: Mapping[str, str]) -> "StaticToken":
        """Build from ``token`` and ``token_variable`` in *options*."""
        return cls(options["token"], options["token_variable"])

    def admit(self, bearer: str) -> Identity | None:
        """Return the identity of the static token, or None for another token.

        Compare in constant time, so the time of a refusal tells a client
        nothing about the token.
        """
        import hmac

        if hmac.compare_digest(bearer.encode(), self._token):
            return Identity(user=USER, method="token")
        return None
