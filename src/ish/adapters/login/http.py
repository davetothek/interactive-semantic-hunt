"""Sign in to an ish server on the path beside ``/mcp``.

A GET of ``/login`` lists the sign-in methods. A POST sends the method,
the user name, and the password in its body, never in the URL, and gets
a login token back. A password goes only over https, or to a server on
this machine, and https checks the certificate of the server with the
certificates of the system. Nothing turns that check off.
"""

import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit

from ish.adapters._http import REDIRECTS, open_without_redirects
from ish.adapters._logins import Saved, TokenFile, TokenFileError
from ish.application.ports.login import LoginError, SignedIn

# A sign-in asks the directory of the server once, which can take a while.
TIMEOUT_SECONDS = 30.0

MCP_PATH = "/mcp"
LOGIN_PATH = "/login"


def login_url(url: str) -> str:
    """Return the sign-in URL beside the MCP URL *url*.

    Raise ``LoginError`` for a URL that is not http or https, that does
    not end in ``/mcp``, or that holds a user name or a password.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or "@" in url:
        raise LoginError(
            "The URL of the server is not an http or https URL with no user name."
        )
    path = parts.path.rstrip("/")
    if not path.endswith(MCP_PATH):
        given = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        raise LoginError(
            f"The URL of the server ends in {MCP_PATH}. Give {given}{MCP_PATH}?"
        )
    path = path.removesuffix(MCP_PATH) + LOGIN_PATH
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _loopback(host: str) -> bool:
    """Return True when *host* names this machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class HttpLogin:
    """Sign in to the server at one MCP URL, and keep its token."""

    def __init__(
        self, url: str, tokens: TokenFile, timeout: float = TIMEOUT_SECONDS
    ) -> None:
        """Sign in to the server at *url*, and keep its token in *tokens*.

        Raise ``LoginError`` for a URL that ``login_url`` refuses.
        """
        self._url = url
        self._login = login_url(url)
        parts = urlsplit(url)
        self._scheme = parts.scheme
        self._hostname = parts.hostname or ""
        self.host = parts.netloc
        self._tokens = tokens
        self._timeout = timeout

    def methods(self) -> list[str]:
        """Return the sign-in methods the server lists. Send no password.

        Read the token file first, so a file that ish refuses stops the
        sign-in before a password is asked for.
        """
        try:
            self._tokens.get(self._url)
        except (OSError, TokenFileError) as exc:
            raise LoginError(str(exc)) from exc
        reply = self._send(None)
        methods = reply.get("methods")
        if not isinstance(methods, list):
            raise LoginError(f"{self.host} did not list its sign-in methods.")
        return [str(method) for method in methods]

    def password_problem(self) -> str:
        """Return why no password may go to this server, or an empty text."""
        if self._scheme == "http" and not _loopback(self._hostname):
            return (
                f"ish sends no password to {self.host} over plain http, because "
                "anyone on the network could read it. Use the https URL of the "
                "server."
            )
        return ""

    def sign_in(self, method: str, user: str, password: str) -> SignedIn:
        """Send the password in the body, and keep the login token."""
        problem = self.password_problem()
        if problem:
            raise LoginError(problem)
        reply = self._send({"method": method, "user": user, "password": password})
        try:
            saved = Saved(
                str(reply["token"]), str(reply["user"]), float(reply["expires"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LoginError(f"{self.host} sent no login token.") from exc
        try:
            self._tokens.put(self._url, saved)
        except (OSError, TokenFileError) as exc:
            raise LoginError(f"Cannot keep the login token: {exc}") from exc
        return SignedIn(saved.user, saved.expires)

    def sign_out(self) -> bool:
        """Delete the login token of the server on this machine."""
        try:
            return self._tokens.remove(self._url)
        except (OSError, TokenFileError) as exc:
            raise LoginError(f"Cannot delete the login token: {exc}") from exc

    def _send(self, body: dict[str, str] | None) -> dict:
        """GET the sign-in path, or POST *body* to it, and return the reply.

        Raise ``LoginError`` with the reason the server gives, or with
        what failed. The text never holds the body.
        """
        import urllib.error
        import urllib.request

        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self._login,
            data=data,
            method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with open_without_redirects(request, self._timeout) as response:
                text = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in REDIRECTS:
                raise LoginError(
                    f"{self.host} answered with a redirect. ish follows no "
                    "redirect, so the password goes to no other address. Give "
                    "the URL the server answers on."
                ) from exc
            if exc.code == 404:
                raise LoginError(
                    f"{self.host} has no sign-in. Update ish on the server, or use "
                    "the token of its operator."
                ) from exc
            raise LoginError(self._reason(exc.code, exc.read())) from exc
        except OSError as exc:
            reason = getattr(exc, "reason", exc)
            raise LoginError(f"Cannot reach {self.host}: {reason}") from exc
        try:
            reply = json.loads(text)
        except ValueError as exc:
            raise LoginError(f"{self.host} did not answer with JSON.") from exc
        if not isinstance(reply, dict):
            raise LoginError(f"{self.host} did not answer with JSON.")
        return reply

    def _reason(self, code: int, body: bytes) -> str:
        """Return the error a refusal of the server names, or its status."""
        try:
            reply = json.loads(body)
        except ValueError:
            reply = None
        error = reply.get("error") if isinstance(reply, dict) else None
        if isinstance(error, str) and error:
            return error
        return f"{self.host} answered HTTP {code}."
