"""Carry MCP messages between a client and the server.

A transport reads the messages a client sends, gives each one to the
server, and writes back the replies. It knows how the messages are
framed and nothing about what they mean, so a new transport changes no
tool and no dispatch code.

To add a transport, write a class with a ``serve(server)`` method beside
``StdioTransport``, and choose it in ``ish.interfaces.mcp.server.main``.
"""

import json
import logging
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
