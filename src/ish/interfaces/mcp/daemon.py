"""Keep one ish server resident for a tree.

``ish-mcp`` serves one host over stdio and dies with it. An editor, an
agent, and a shell each spawn their own, so one tree gets several
servers, several loaded models, and several threads walking the same
files, and nothing runs at all until an editor opens.

``ish-serve`` binds a Unix socket for a tree and serves every client of
it through the same tools and the same protocol. It refreshes the tree
as soon as it starts and then every ``refresh_seconds``, so the index is
current before anyone asks, and it outlives any client. The command
line talks to it directly. ``ish-mcp`` joins it when it finds the
socket, so every editor and agent host shares it without a change.
"""

import argparse
import logging
import sys
from pathlib import Path

from ish.interfaces.log import setup_logging
from ish.interfaces.mcp import transport
from ish.interfaces.mcp.protocol import Server
from ish.interfaces.mcp.server import SERVER_NAME, IshTools, installed_version
from ish.settings import load_settings

log = logging.getLogger("ish.mcp")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    """Read the lifecycle options. Everything else comes from the config."""
    parser = argparse.ArgumentParser(
        prog="ish-serve",
        description=(
            "Keep one ish server resident for a tree, and serve every client "
            "of it over a Unix socket."
        ),
    )
    parser.add_argument(
        "tree",
        nargs="?",
        default=".",
        help=(
            "The tree to serve (default: the working directory). "
            "The server reads its config there."
        ),
    )
    parser.add_argument(
        "--socket",
        default=None,
        metavar="PATH",
        help="Listen at PATH rather than where the clients look for this tree.",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "Stop after this long with no client connected. 0 keeps the server running."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Log more. Twice for debug output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the resident server. Return an exit code."""
    args = _parse(argv)
    root = Path(args.tree).expanduser().resolve()
    if not root.is_dir():
        print(f"ish-serve: {root} is not a directory", file=sys.stderr)
        return 1
    settings = load_settings(start=root)
    setup_logging(verbosity=max(settings.verbosity, args.verbose), color=False)

    path = Path(args.socket).expanduser() if args.socket else transport.socket_for(root)
    tools = IshTools(settings, root)
    server = Server(SERVER_NAME, installed_version(), tools.tools())
    listening = transport.SocketServer(server, path, idle_seconds=args.idle_seconds)
    try:
        # Take the socket before any work, so a second server for the
        # tree leaves without starting a refresh of its own.
        listening.bind()
        tools.watch(root)
        listening.serve_forever()
    except transport.ServerRunning as exc:
        log.info("%s", exc)
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        tools.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
