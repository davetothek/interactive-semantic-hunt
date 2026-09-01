"""Speak the Model Context Protocol over stdio.

MCP frames JSON-RPC 2.0 messages one per line on stdin and stdout. That
is small enough to implement directly, which keeps ish free of a
protocol dependency.

Nothing may write to stdout except a protocol message. Logging goes to
stderr, which the host shows as server output.
"""

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any, TextIO

log = logging.getLogger(__name__)

# Protocol revisions this server understands. Echo back whichever the
# client asked for when it is one of these, so a newer host still works.
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = SUPPORTED_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


class Tool:
    """One callable the host may invoke."""

    def __init__(
        self,
        name: str,
        description: str,
        schema: Mapping[str, Any],
        handler: Callable[[Mapping[str, Any]], str],
    ) -> None:
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler

    def describe(self) -> dict[str, Any]:
        """Render the entry that ``tools/list`` returns."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
        }


class Server:
    """Dispatch JSON-RPC messages to a set of tools."""

    def __init__(self, name: str, version: str, tools: list[Tool]) -> None:
        self._name = name
        self._version = version
        self._tools = {tool.name: tool for tool in tools}

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def serve(self, stream_in: TextIO, stream_out: TextIO) -> None:
        """Read messages until the host closes stdin."""
        log.info("MCP server ready with %d tools", len(self._tools))
        for line in stream_in:
            line = line.strip()
            if not line:
                continue
            reply = self.handle_line(line)
            if reply is not None:
                stream_out.write(json.dumps(reply) + "\n")
                stream_out.flush()
        log.info("MCP server stopped")

    def handle_line(self, line: str) -> dict[str, Any] | None:
        """Handle one raw message. Return the reply, or None to stay quiet."""
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            return _error(None, PARSE_ERROR, f"Cannot parse the message: {exc}")

        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "Expected a JSON object.")

        return self.handle(message)

    def handle(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """Dispatch one decoded message."""
        method = message.get("method")
        request_id = message.get("id")

        # A message with no id is a notification, which takes no reply.
        if request_id is None:
            log.debug("Notification: %s", method)
            return None

        try:
            result = self._dispatch(str(method), message.get("params") or {})
        except _MethodNotFound:
            return _error(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
        except Exception as exc:
            log.exception("Failed to handle %s", method)
            return _error(request_id, INTERNAL_ERROR, str(exc))

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def _dispatch(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "tools/list":
            return {"tools": [tool.describe() for tool in self._tools.values()]}
        if method == "tools/call":
            return self._call(params)
        if method == "ping":
            return {}
        raise _MethodNotFound

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Agree a protocol revision and announce what this server offers."""
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else DEFAULT_VERSION
        log.info("Client asked for protocol %s, replying with %s", asked, version)
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self._name, "version": self._version},
        }

    def _call(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Run one tool and wrap whatever it returns as text content.

        Report a tool failure through ``isError`` rather than a JSON-RPC
        error, so the host can show the reason to the model.
        """
        name = params.get("name")
        tool = self._tools.get(str(name))
        if tool is None:
            return _tool_error(f"Unknown tool: {name}")

        try:
            text = tool.handler(params.get("arguments") or {})
        except Exception as exc:
            log.exception("Tool %s failed", name)
            return _tool_error(str(exc))

        return {"content": [{"type": "text", "text": text}], "isError": False}


class _MethodNotFound(Exception):
    """Raise for a JSON-RPC method this server does not implement."""


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC error reply."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_error(message: str) -> dict[str, Any]:
    """Build a failed tool result."""
    return {"content": [{"type": "text", "text": message}], "isError": True}
