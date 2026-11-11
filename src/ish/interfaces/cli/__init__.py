"""The ``ish`` command line.

``run`` is the entry point of ``ish``. It reads the first word and hands
the rest of the line to the program that word names. It imports that
program and no other, so a completion does not pay for a search, and a
search does not pay for a server.
"""

import sys


def run(argv: list[str] | None = None) -> int:
    """Run the program the first word names. Return an exit code.

    ``mcp``, ``serve``, and ``complete`` are programs of their own, which
    ``ish-mcp``, ``ish-serve``, and ``ish-complete`` also start. ``remote``
    is the program that reads the tree of a server. Every other line
    goes to the search program. The name is not ``main``,
    because that is the module of the search program in this package.
    """
    words = list(sys.argv[1:] if argv is None else argv)
    command = words[0] if words else ""
    if command == "complete":
        from ish.interfaces.cli import complete

        return complete.main(words[1:])
    if command == "serve":
        from ish.interfaces import daemon

        return daemon.main(words[1:], prog="ish serve")
    if command == "mcp":
        from ish.interfaces.mcp import server

        return server.main(words[1:], prog="ish mcp")
    if command == "remote":
        from ish.interfaces.cli import remote

        return remote.main(words[1:])
    from ish.interfaces.cli import main

    return main.main(words)
