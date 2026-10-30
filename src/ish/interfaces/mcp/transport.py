"""Carry MCP messages between a client and the server.

A transport reads the messages a client sends, gives each one to the
server, and writes back the replies. It knows how the messages are
framed and nothing about what they mean, so a new transport changes no
tool and no dispatch code.

To add a transport, write a class with a ``serve(server)`` method beside
``StdioTransport`` and ``HttpTransport``, and choose it in
``ish.interfaces.mcp.server.main``.
"""

import json
import logging
import threading
from typing import Any, Protocol, TextIO, runtime_checkable

log = logging.getLogger(__name__)


class Handler(Protocol):
    """What a transport hands each message to."""

    def handle_line(self, line: str) -> dict[str, Any] | None:
        """Handle one raw message. Return the reply, or None to stay quiet."""
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


TOKEN_VARIABLE = "ISH_MCP_TOKEN"
MCP_PATH = "/mcp"

# A JSON-RPC message from a client is small. A body beyond this is not
# one, and reading it would let a client fill the memory of the server.
MAX_BODY_BYTES = 1_000_000


class HttpTransport:
    """Carry MCP over Streamable HTTP, behind one shared bearer token.

    A client posts one JSON-RPC message to ``/mcp`` and reads the reply
    in the response body. A notification gets 202 and no body. Every
    answer fits in one reply, so the server opens no event stream, and a
    GET is refused. Each request runs on its own thread, so a slow
    answer for one client does not hold up another.

    A request without the token, or with a wrong one, gets 401 and runs
    nothing.
    """

    def __init__(self, host: str, port: int, token: str) -> None:
        """Listen on *host* and *port*, and accept *token* only.

        Raise ``ValueError`` without a token. A server on a network with
        no token would answer anyone who can reach it.
        """
        if not token:
            raise ValueError(
                f"Set {TOKEN_VARIABLE} before serving MCP over HTTP. "
                "Every request must carry it as a bearer token."
            )
        self._host = host
        self._port = port
        self._expected = f"Bearer {token}".encode()
        self._httpd: Any = None
        self.address: tuple[str, int] | None = None
        # Set once the socket is bound, so a caller that asked for port 0
        # can read the port it got before it sends a request.
        self.ready = threading.Event()

    def serve(self, server: Handler) -> None:
        """Answer requests until ``stop`` is called or the process is interrupted."""
        from http.server import ThreadingHTTPServer

        self._httpd = ThreadingHTTPServer(
            (self._host, self._port), self._handler_for(server)
        )
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
        import hmac
        from http.server import BaseHTTPRequestHandler

        expected = self._expected

        class _Request(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if self.path != MCP_PATH:
                    self._reply(404)
                    return
                given = (self.headers.get("Authorization") or "").encode()
                if not hmac.compare_digest(given, expected):
                    self._reply(401, headers={"WWW-Authenticate": "Bearer"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY_BYTES:
                    self._reply(413)
                    return
                line = self.rfile.read(length).decode("utf-8", "replace")
                reply = server.handle_line(line)
                if reply is None:
                    self._reply(202)
                    return
                self._reply(200, json.dumps(reply).encode())

            def do_GET(self) -> None:
                self._reply(405, headers={"Allow": "POST"})

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
