"""Carry MCP messages between a client and the server.

A transport reads the messages a client sends, gives each one to the
server, and writes back the replies. It knows how the messages are
framed and nothing about what they mean, so a new transport changes no
tool and no dispatch code.

To add a transport, write a class with a ``serve(server)`` method beside
``StdioTransport`` and ``HttpTransport``, and choose it in
``ish.interfaces.mcp.server.main``.
"""

import ipaddress
import json
import logging
import sys
import threading
from collections.abc import Callable, Sequence
from ipaddress import IPv4Network, IPv6Address, IPv6Network
from typing import Any, Protocol, TextIO, runtime_checkable

from ish.application.ports.authenticator import (
    Identity,
    SignInLimited,
    SignInRefused,
    SignInUnavailable,
)
from ish.settings import MCP_TOKEN_ENV_VAR

log = logging.getLogger(__name__)


class Handler(Protocol):
    """What a transport hands each message to."""

    def handle_line(self, line: str, who: str = "") -> dict[str, Any] | None:
        """Handle one raw message. Return the reply, or None to stay quiet.

        *who* is the user a transport admitted the message for, or empty
        when the transport admits nobody, as stdio does.
        """
        ...


class Granted(Protocol):
    """A login token a desk gave."""

    @property
    def token(self) -> str: ...

    @property
    def user(self) -> str: ...

    @property
    def expires(self) -> float: ...


class Desk(Protocol):
    """What signs a user in on the sign-in path."""

    def methods(self) -> list[str]:
        """Return the names of the sign-in methods."""
        ...

    def sign_in(self, method: str, user: str, password: str) -> Granted:
        """Return a login token for *user*, or raise."""
        ...


@runtime_checkable
class Transport(Protocol):
    """Contract for moving MCP messages to and from a client."""

    def serve(self, server: Handler) -> None:
        """Serve messages until the client goes away."""
        ...


class StdioTransport:
    """Frame JSON-RPC messages one per line on a pair of text streams.

    Nothing may write to the output stream except a protocol message.
    Logging goes to stderr, which the host shows as server output.
    """

    def __init__(self, stream_in: TextIO, stream_out: TextIO) -> None:
        self._in = stream_in
        self._out = stream_out

    def serve(self, server: Handler) -> None:
        """Read messages until the client closes the input stream."""
        for line in self._in:
            line = line.strip()
            if not line:
                continue
            reply = server.handle_line(line)
            if reply is not None:
                self._out.write(json.dumps(reply) + "\n")
                self._out.flush()


TOKEN_VARIABLE = MCP_TOKEN_ENV_VAR
MCP_PATH = "/mcp"
# Beside `/mcp`, so a reverse proxy that forwards one forwards both.
LOGIN_PATH = "/login"
# Where another service asks whether a token is admitted, such as the web
# server of the documents through auth_request, or a reverse proxy through
# its forward authentication.
AUTH_PATH = "/auth"
# The headers of an answer of /auth that say who was admitted, so the
# service that asked can pass them on and log them.
USER_HEADER = "X-Ish-User"
METHOD_HEADER = "X-Ish-Method"

# A JSON-RPC message from a client is small. A body beyond this is not
# one, and reading it would let a client fill the memory of the server.
MAX_BODY_BYTES = 1_000_000

# The most connections the server serves at once, and how long one may
# send nothing.
MAX_CONNECTIONS = 64
SOCKET_SECONDS = 30


def _who(identity: Identity) -> dict[str, str]:
    """Return the headers that name *identity*, with each value safe in a header.

    A user name comes from a sign-in. A name with a character that is no
    printable ASCII, such as a line break, could end the header and start
    another, so it is left out.
    """
    headers = {}
    for name, value in ((USER_HEADER, identity.user), (METHOD_HEADER, identity.method)):
        if value and value.isascii() and value.isprintable():
            headers[name] = value
    return headers


class HttpTransport:
    """Carry MCP over Streamable HTTP, behind a bearer token.

    A client posts one JSON-RPC message to ``/mcp`` and reads the reply
    in the response body. A notification gets 202 and no body. Every
    answer fits in one reply, so the server opens no event stream, and a
    GET is refused. Each request runs on its own thread, so a slow
    answer for one client does not hold up another.

    A request without a bearer token, or with one that *admit* does not
    know, gets 401 and runs nothing. The transport knows no method of
    authentication. It asks *admit* about each token.

    With a *desk*, ``/login`` signs a user in. A GET lists the sign-in
    methods and needs no token. A POST carries the method, the user name,
    and the password in its body, and gets a login token back. No line
    of a log holds the body.

    A GET of ``/auth`` gets 204 for a token *admit* admits and 401 for any
    other, and runs nothing. A web server or a reverse proxy in front of
    other files asks it, so they need the token that ``/mcp`` needs. The
    204 names the user and the method in ``X-Ish-User`` and
    ``X-Ish-Method``, for the one that asked to pass on.

    With *trusted* networks, a request from an address outside all of
    them gets 403 and runs nothing, so a password comes only through the
    reverse proxy that holds the certificate.
    """

    def __init__(
        self,
        host: str,
        port: int,
        admit: Callable[[str], Identity | None],
        desk: Desk | None = None,
        *,
        trusted: Sequence[IPv4Network | IPv6Network] = (),
    ) -> None:
        """Listen on *host* and *port*, and run a request *admit* admits."""
        self._host = host
        self._port = port
        self._admit = admit
        self._desk = desk
        self._trusted = tuple(trusted)
        self._httpd: Any = None
        self.address: tuple[str, int] | None = None
        # Set once the socket is bound, so a caller that asked for port 0
        # can read the port it got before it sends a request.
        self.ready = threading.Event()

    def serve(self, server: Handler) -> None:
        """Answer requests until ``stop`` is called or the process is interrupted."""
        from http.server import ThreadingHTTPServer

        open_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

        class _Quiet(ThreadingHTTPServer):
            # Connections the system holds before the server takes them.
            request_queue_size = 64

            def process_request(self, request: Any, client_address: Any) -> None:
                # A client can open connections before any sign-in. Close
                # the ones over the cap, so they hold no thread.
                if not open_slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    open_slots.release()
                    raise

            def process_request_thread(self, request: Any, client_address: Any) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    open_slots.release()

            def handle_error(self, request: Any, client_address: Any) -> None:
                # The default prints the traceback with the text of the
                # error, and a sign-in method can put a password in it.
                # Name the type of the error alone, at every verbosity.
                failure = sys.exc_info()[0]
                name = failure.__name__ if failure else "an error"
                log.error("A request from %s failed with %s.", client_address[0], name)

        self._httpd = _Quiet((self._host, self._port), self._handler_for(server))
        self._httpd.daemon_threads = True
        host, port = self._httpd.server_address[:2]
        self.address = (str(host), int(port))
        log.info("Serving MCP on http://%s:%d%s", host, port, MCP_PATH)
        self.ready.set()
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()

    def stop(self) -> None:
        """Stop serving. Call from another thread than ``serve``."""
        if self._httpd is not None:
            self._httpd.shutdown()

    def _handler_for(self, server: Handler) -> type:
        from http.server import BaseHTTPRequestHandler

        admit = self._admit
        desk = self._desk
        trusted = self._trusted

        class _Request(BaseHTTPRequestHandler):
            # A client that sends nothing for this long loses its
            # connection, so a stalled request holds no thread for ever.
            timeout = SOCKET_SECONDS

            def do_POST(self) -> None:
                if not self._from_trusted():
                    return
                if self.path == LOGIN_PATH and desk is not None:
                    self._sign_in(desk)
                    return
                if self.path != MCP_PATH:
                    self._reply(404)
                    return
                identity = self._admitted()
                if identity is None:
                    return
                body = self._body()
                if body is None:
                    return
                reply = server.handle_line(
                    body.decode("utf-8", "replace"), who=identity.user
                )
                if reply is None:
                    self._reply(202)
                    return
                self._reply(200, json.dumps(reply).encode())

            def do_GET(self) -> None:
                if not self._from_trusted():
                    return
                if self.path == LOGIN_PATH and desk is not None:
                    self._json(200, {"methods": desk.methods()})
                    return
                if self.path == AUTH_PATH:
                    identity = self._admitted()
                    if identity is not None:
                        self._reply(204, headers=_who(identity))
                    return
                self._reply(405, headers={"Allow": "POST"})

            def _from_trusted(self) -> bool:
                """Return True for a request the server may read, or reply 403.

                With no trusted network, every address may ask. An IPv4
                address that IPv6 carries counts as the IPv4 address.
                """
                if not trusted:
                    return True
                try:
                    peer = ipaddress.ip_address(self.client_address[0])
                except ValueError:
                    peer = None
                if isinstance(peer, IPv6Address) and peer.ipv4_mapped is not None:
                    peer = peer.ipv4_mapped
                if peer is not None and any(peer in net for net in trusted):
                    return True
                self._reply(403)
                return False

            def _admitted(self) -> Identity | None:
                """Return who the bearer token of the request is, or reply and None.

                Reply 401 for no token, or one *admit* does not know, and
                500 when the check itself fails.
                """
                scheme, _, bearer = (self.headers.get("Authorization") or "").partition(
                    " "
                )
                try:
                    identity = admit(bearer) if scheme == "Bearer" else None
                except Exception as exc:
                    log.error(
                        "The check of a token failed with %s.", type(exc).__name__
                    )
                    self._json(500, {"error": SERVER_FAILED})
                    return None
                if identity is None:
                    self._reply(401, headers={"WWW-Authenticate": "Bearer"})
                return identity

            def _body(self) -> bytes | None:
                """Return the body of the request, or reply and return None.

                Reply 400 for a length that is not a count, and 413 for one
                above ``MAX_BODY_BYTES``. A read of a negative length would
                read until the client stops, so it is never made.
                """
                text = self.headers.get("Content-Length") or "0"
                if not (text.isascii() and text.isdigit() and len(text) <= 12):
                    self._reply(400)
                    return None
                length = int(text)
                if length > MAX_BODY_BYTES:
                    self._reply(413)
                    return None
                return self.rfile.read(length)

            def _sign_in(self, desk: Desk) -> None:
                """Sign a user in, and reply with the login token or the reason."""
                body = self._body()
                if body is None:
                    return
                try:
                    asked = json.loads(body)
                except ValueError:
                    asked = None
                if not isinstance(asked, dict) or not all(
                    isinstance(asked.get(key), str)
                    for key in ("method", "user", "password")
                ):
                    self._json(400, {"error": SIGN_IN_BODY})
                    return
                try:
                    granted = desk.sign_in(
                        asked["method"], asked["user"], asked["password"]
                    )
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                except SignInRefused as exc:
                    self._json(401, {"error": str(exc)})
                    return
                except SignInUnavailable as exc:
                    self._json(503, {"error": str(exc)})
                    return
                except SignInLimited as exc:
                    self._json(429, {"error": str(exc)})
                    return
                except Exception as exc:
                    # The text of an error the method did not expect can
                    # hold the password. Name its type alone.
                    log.error(
                        "A sign-in with %s failed with %s.",
                        printable(asked["method"]),
                        type(exc).__name__,
                    )
                    self._json(500, {"error": SERVER_FAILED})
                    return
                log.info(
                    "Signed %s in, until %s",
                    printable(granted.user),
                    _when(granted.expires),
                )
                self._json(
                    200,
                    {
                        "token": granted.token,
                        "user": granted.user,
                        "expires": granted.expires,
                    },
                )

            def _json(self, status: int, data: dict[str, Any]) -> None:
                self._reply(status, json.dumps(data).encode())

            def _reply(
                self, status: int, body: bytes = b"", headers: dict | None = None
            ) -> None:
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                if body:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                log.debug("%s %s", self.address_string(), format % args)

        return _Request


# What a client reads when the server failed. The log of the server
# names the type of the error, and never its text.
SERVER_FAILED = "The server failed. Its log names the type of the error."


def printable(text: str) -> str:
    """Return *text* with each character that is not printable escaped.

    A name a client sends goes into a log line. A line break in it would
    write a second line that looks like the server wrote it.
    """
    return "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in text)


# What a POST to the sign-in path must hold.
SIGN_IN_BODY = (
    "Send a JSON object with the method, the user name, and the password, each as text."
)


def _when(seconds: float) -> str:
    """Return the moment *seconds* after the epoch, in UTC, to the minute."""
    import datetime

    moment = datetime.datetime.fromtimestamp(seconds, datetime.UTC)
    return moment.strftime("%Y-%m-%d %H:%M UTC")
