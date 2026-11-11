"""Call the tools of an ish MCP server over HTTP.

A run that reads the chunks of a server needs a few tool calls and
nothing else. The client speaks the JSON-RPC that the server serves over
HTTP: it opens the session with ``initialize``, calls the tool, and
returns the text the tool answered with. It uses the standard library
only, as the Ollama adapter does, so the run loads no client package.
"""

import json
import urllib.error
import urllib.request
from importlib.metadata import version
from typing import Any
from urllib.parse import urlsplit

from ish.application.ports.chunk_source import ChunkSourceError

PROTOCOL_VERSION = "2025-06-18"

# A search answers in well under a second. A server that opens a large
# index for its first call takes longer, so give it a minute.
TIMEOUT_SECONDS = 60.0


class RemoteClient:
    """Call the tools of one MCP server, behind one bearer token."""

    def __init__(
        self,
        url: str,
        token: str,
        token_variable: str,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        """Talk to the server at *url*.

        *token_variable* is the environment variable the token came from.
        An error about the token names it, and never shows the token.
        Raise ``ChunkSourceError`` for a URL that is not http or https,
        for a URL that holds a user name or a password, and for an
        empty token.
        """
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            # An address with an `@` can hold a password. Do not show it.
            shown = "The URL of the server" if "@" in url else repr(url)
            raise ChunkSourceError(f"{shown} is not an http or https URL.")
        if "@" in parts.netloc:
            # Each error and the usage line name the server. A password
            # in the address would reach a log, so refuse it unseen.
            raise ChunkSourceError(
                "The URL of the server holds a user name or a password. "
                f"Give the URL alone. The token goes in {token_variable}."
            )
        if not token:
            raise ChunkSourceError(f"Set {token_variable} to the token of {url}.")
        self._url = url
        self.host = parts.netloc
        self._token = token
        self._token_variable = token_variable
        self._timeout = timeout
        self._last_id = 0
        self._open = False
        # What the server called its version when the session opened.
        self.server_version = ""

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        """Return the text that *tool* answers with.

        Raise ``ChunkSourceError`` when the server cannot be reached,
        refuses the token, or reports that the tool failed.
        """
        if not self._open:
            opened = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "ish",
                        "version": version("interactive-semantic-hunt"),
                    },
                },
            )
            info = opened.get("serverInfo")
            if isinstance(info, dict):
                self.server_version = str(info.get("version") or "")
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._open = True
        result = self._request("tools/call", {"name": tool, "arguments": arguments})
        text = "".join(
            str(item.get("text", ""))
            for item in result.get("content", [])
            if item.get("type") == "text"
        )
        if result.get("isError"):
            raise ChunkSourceError(text or f"The {tool} tool failed on {self._url}.")
        return text

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one request and return its result."""
        self._last_id += 1
        reply = self._post(
            {"jsonrpc": "2.0", "id": self._last_id, "method": method, "params": params}
        )
        if not isinstance(reply, dict):
            raise ChunkSourceError(f"{self._url} sent no reply to {method}.")
        if "error" in reply:
            message = reply["error"].get("message", "unknown error")
            raise ChunkSourceError(f"{self._url} refused {method}: {message}")
        return reply.get("result") or {}

    def _post(self, message: dict[str, Any]) -> Any:
        """Post *message* and return the JSON reply, or None for an empty one."""
        request = urllib.request.Request(
            self._url,
            data=json.dumps(message).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ChunkSourceError(
                    f"{self._url} refused the token in {self._token_variable}."
                ) from exc
            raise ChunkSourceError(f"{self._url} answered HTTP {exc.code}.") from exc
        except OSError as exc:
            reason = getattr(exc, "reason", exc)
            raise ChunkSourceError(f"Cannot reach {self._url}: {reason}") from exc
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as exc:
            raise ChunkSourceError(f"{self._url} did not answer with JSON.") from exc
