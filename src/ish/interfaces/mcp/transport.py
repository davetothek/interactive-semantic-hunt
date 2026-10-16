"""Serve and reach the MCP server over a Unix socket.

The stdio transport binds a server to the process that spawned it. An
editor, an agent, and a shell each spawn their own, so one tree gets
three servers, three loaded models, and three threads walking the same
files. A socket lets one server answer every client, and lets the
server outlive any of them, so the index is current before anyone asks.

The socket of a tree sits under the runtime directory of the user and
is named after the tree, the way its index file is. A client walks up
from the path it asks about until it finds one, so a question about a
subdirectory reaches the server of the tree that holds it.
"""

import contextlib
import errno
import hashlib
import json
import logging
import os
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from ish.interfaces.mcp.protocol import Server

log = logging.getLogger("ish.mcp")

SOCKET_DIRNAME = "ish"
SOCKET_SUFFIX = ".sock"

# How many characters of the tree's name the socket's name keeps. A
# socket path is limited to about a hundred bytes.
NAME_CHARS = 40

# How long a client waits for the server to answer one call. A query
# on a model the backend has unloaded takes seconds, not minutes.
CALL_SECONDS = 120.0

# How long a connection attempt waits, when the question is only
# whether a server listens.
PROBE_SECONDS = 1.0

# How often the server looks at the clock while it waits for a client.
ACCEPT_SECONDS = 1.0


class ServerRunning(Exception):
    """Raise when a server already listens at the socket."""


class RpcError(Exception):
    """Raise when the server answers with an error, or with no message."""


class ToolError(Exception):
    """Raise when a tool ran and reported a failure."""


def runtime_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory the sockets live in."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / SOCKET_DIRNAME
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tempfile.gettempdir()) / f"{SOCKET_DIRNAME}-{uid}"


def socket_for(root: Path, environ: Mapping[str, str] | None = None) -> Path:
    """Return where a server for *root* listens.

    Name the socket the way the index file is named: the tree's name,
    then a digest of its full path, so two trees of one name stay apart.
    """
    resolved = root.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    name = resolved.name[:NAME_CHARS] or "root"
    return runtime_dir(environ) / f"{name}-{digest}{SOCKET_SUFFIX}"


def find_socket(path: Path, environ: Mapping[str, str] | None = None) -> Path | None:
    """Return the socket of the nearest server whose tree holds *path*."""
    resolved = path.resolve()
    for candidate in (resolved, *resolved.parents):
        wanted = socket_for(candidate, environ)
        if wanted.is_socket():
            return wanted
    return None


def answers(path: Path) -> bool:
    """Return True when a server accepts a connection at *path*."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(PROBE_SECONDS)
    try:
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _bind(path: Path) -> socket.socket:
    """Return a socket bound at *path*, taking over one a stopped server left.

    A server that was killed leaves its socket file behind. Nothing
    answers there, so the file is taken away and the path bound again.
    A server that answers keeps its socket, and the caller stops.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    listening = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            listening.bind(str(path))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            if answers(path):
                raise ServerRunning(f"A server already listens at {path}.") from None
            log.info("Taking over the socket a stopped server left at %s", path)
            path.unlink()
            listening.bind(str(path))
    except BaseException:
        listening.close()
        raise
    return listening


class SocketServer:
    """Accept connections on a Unix socket, and serve each as its own session.

    Every connection gets a thread that runs the same line loop the
    stdio transport runs, over the same ``Server``. The tools behind it
    are shared, so every client reads the same warm index.
    """

    def __init__(
        self,
        server: Server,
        path: Path,
        *,
        idle_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._server = server
        self._path = path
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._listening: socket.socket | None = None
        self._lock = threading.Lock()
        self._connected = 0
        self._last_activity = clock()
        self._stopping = threading.Event()
        # Set once the socket is bound and listening, for a caller that
        # waits to connect.
        self.ready = threading.Event()

    def bind(self) -> None:
        """Take the socket now, so a caller can do work before clients come.

        Raise ``ServerRunning`` when a server already listens there.
        Serving later binds here on its own when nobody did.
        """
        if self._listening is not None:
            return
        self._listening = _bind(self._path)
        self._listening.listen()
        self._listening.settimeout(ACCEPT_SECONDS)
        log.info("Listening at %s", self._path)
        self.ready.set()

    def serve_forever(self) -> None:
        """Bind, then serve clients until stopped or idle for long enough.

        Stop with ``idle_seconds`` of nobody connected, when that is
        above zero, so a forgotten server does not hold the model and
        the index open for ever. Zero keeps serving.
        """
        self.bind()
        listening = self._listening
        assert listening is not None
        try:
            while not self._stopping.is_set():
                try:
                    connection, _address = listening.accept()
                except TimeoutError:
                    if self._idle():
                        log.info("No client for %s s. Stopping.", self._idle_seconds)
                        return
                    continue
                except OSError:
                    # The listening socket was closed from another thread.
                    return
                self._begin()
                threading.Thread(
                    target=self._serve_one,
                    args=(connection,),
                    name="ish-client",
                    daemon=True,
                ).start()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Stop accepting clients, and take the socket file away."""
        self._stopping.set()
        listening, self._listening = self._listening, None
        if listening is not None:
            listening.close()
        with contextlib.suppress(OSError):
            self._path.unlink()

    def _idle(self) -> bool:
        with self._lock:
            quiet = self._clock() - self._last_activity
            return 0 < self._idle_seconds < quiet and self._connected == 0

    def _begin(self) -> None:
        with self._lock:
            self._connected += 1

    def _end(self) -> None:
        with self._lock:
            self._connected -= 1
            self._last_activity = self._clock()

    def _serve_one(self, connection: socket.socket) -> None:
        """Run the line loop for one client until it hangs up."""
        try:
            with (
                connection,
                connection.makefile("r", encoding="utf-8") as reader,
                connection.makefile("w", encoding="utf-8") as writer,
            ):
                self._server.serve(reader, writer)
        except OSError as exc:
            log.debug("A client connection ended: %s", exc)
        finally:
            self._end()


def connect(path: Path) -> socket.socket | None:
    """Open a connection to the server at *path*, or return None when none answers.

    Take away a socket file nobody listens on, so the next check for a
    server does not mistake it for one. The connection waits
    ``CALL_SECONDS`` for an answer. A caller that keeps it for a whole
    session clears that.
    """
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(CALL_SECONDS)
    try:
        connection.connect(str(path))
    except ConnectionRefusedError:
        connection.close()
        log.info("Nothing listens at %s. Removing it.", path)
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    except OSError as exc:
        connection.close()
        log.debug("Cannot connect to %s: %s", path, exc)
        return None
    return connection


class Client:
    """One connection to a server, speaking JSON-RPC a line at a time.

    The server answers every call whether or not the handshake ran, so
    a short-lived client skips ``initialize`` and saves a round trip.
    """

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._reader = connection.makefile("r", encoding="utf-8")
        self._writer = connection.makefile("w", encoding="utf-8")
        self._next_id = 0

    @classmethod
    def connect(cls, path: Path) -> "Client | None":
        """Connect to the server at *path*, or return None when none answers."""
        connection = connect(path)
        return None if connection is None else cls(connection)

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Hang up."""
        for stream in (self._writer, self._reader):
            with contextlib.suppress(OSError):
                stream.close()
        self._connection.close()

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        """Send one request and return its result.

        Raise ``RpcError`` for an error reply or a reply that is not one,
        and ``OSError`` when the connection fails or the server hangs up.
        """
        self._next_id += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params is not None:
            message["params"] = dict(params)
        self._writer.write(json.dumps(message) + "\n")
        self._writer.flush()
        line = self._reader.readline()
        if not line:
            raise ConnectionResetError("The server hung up.")
        try:
            reply = json.loads(line)
        except ValueError as exc:
            raise RpcError(
                f"The server sent something that is not a reply: {exc}"
            ) from exc
        if not isinstance(reply, dict):
            raise RpcError("The server sent something that is not a reply.")
        if "error" in reply:
            raise RpcError(str(reply["error"].get("message", "unknown error")))
        return reply.get("result")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> str:
        """Run one tool and return its text. Raise ``ToolError`` when it failed."""
        result = self.call("tools/call", {"name": name, "arguments": dict(arguments)})
        content = (result or {}).get("content") or [{}]
        text = str(content[0].get("text", ""))
        if (result or {}).get("isError"):
            raise ToolError(text)
        return text


def bridge(
    stream_in: TextIO,
    stream_out: TextIO,
    connection: socket.socket,
    *,
    default_path: Path | None = None,
) -> None:
    """Carry lines between a host on the streams and a server on *connection*.

    Return when either side closes. The host closing its end tells the
    server the session is over. The server closing its end ends the
    session, and the host starts another when it next asks.

    A call that names no path means the directory of the host, as it
    does without a shared server, so fill *default_path* in before the
    line goes. The server would otherwise read it as its own tree.
    """
    connection.settimeout(None)
    to_server = connection.makefile("w", encoding="utf-8")
    from_server = connection.makefile("r", encoding="utf-8")

    def forward() -> None:
        try:
            for line in stream_in:
                to_server.write(_with_path(line, default_path))
                to_server.flush()
            connection.shutdown(socket.SHUT_WR)
        except (OSError, ValueError):
            # The server went first. The other direction notices too,
            # and the session ends there.
            return

    threading.Thread(target=forward, name="ish-bridge", daemon=True).start()
    try:
        for line in from_server:
            stream_out.write(line)
            stream_out.flush()
    except OSError as exc:
        log.debug("The connection to the server ended: %s", exc)
    finally:
        with contextlib.suppress(OSError):
            connection.close()


def _with_path(line: str, default_path: Path | None) -> str:
    """Return *line* with *default_path* filled into a tool call that names none.

    Leave every other line as it is, a malformed one included. The
    server answers those itself.
    """
    if default_path is None or "tools/call" not in line:
        return line
    try:
        message = json.loads(line)
        if message["method"] != "tools/call":
            return line
        arguments = message["params"].setdefault("arguments", {})
    except (ValueError, KeyError, TypeError, AttributeError):
        return line
    if not isinstance(arguments, dict):
        return line
    arguments.setdefault("path", str(default_path))
    return json.dumps(message) + "\n"
