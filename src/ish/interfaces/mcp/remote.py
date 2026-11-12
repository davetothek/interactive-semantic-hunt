"""Serve the tools that read a server, on stdio, for the tree of a server.

``ish remote mcp`` starts this. ``search_code`` and ``ask`` behave as
``ish remote search`` and ``ish remote ask`` do: the server searches its
own tree and returns chunks, and the language model of this machine
writes the answer. ``list_chunks``, ``index_status``, and
``complete_filter`` pass the call to the server. An agent or an editor
gets the tools of a server with the model login of the user. Only ``ish
remote mcp`` imports this module.
"""

import json
from collections.abc import Mapping
from typing import Any

from ish.interfaces.mcp.protocol import Tool
from ish.interfaces.mcp.server import (
    NO_RESULTS,
    answer_data,
    ask_tool,
    complete_tool,
    list_tool,
    narrowing_of,
    search_tool,
    status_tool,
    turns_of,
)
from ish.interfaces.python.remote import RemoteIsh
from ish.settings import Settings

# A server searches its own tree, so a path of this machine means nothing there.
NO_PATH = (
    "The server searches its own tree, so a call takes no path. "
    "Use under to narrow a search."
)


class RemoteTools:
    """Answer the tool calls of one host from the server ``remote`` names."""

    def __init__(self, settings: Settings) -> None:
        """Read the server at ``settings.remote``. Send no request yet.

        Raise ``ChunkSourceError`` for a URL that is not http or https,
        and for a token that is not set.
        """
        self._settings = settings
        self._remote = RemoteIsh(settings)

    @property
    def host(self) -> str:
        """Return the name of the server."""
        return self._remote.host

    def search(self, arguments: Mapping[str, Any]) -> str:
        """Return the results of the server for a query, as lines."""
        query = _text(arguments, "query")
        lines = self._remote.search(
            query,
            int(arguments.get("limit") or self._settings.limit),
            shape=str(arguments.get("format") or ""),
            **narrowing_of(arguments),
        )
        return lines or f"{NO_RESULTS} {query!r} on {self.host}."

    def ask(self, arguments: Mapping[str, Any]) -> str:
        """Answer a question from the chunks of the server, as JSON.

        The reply is the one a local ``ask`` gives, with ``server`` added:
        the host and the version of the server that searched.
        """
        question = _text(arguments, "question")
        brief = bool(arguments.get("brief"))
        answer = self._remote.ask(
            question,
            brief=brief,
            turns=turns_of(arguments.get("ask_turns")),
            **narrowing_of(arguments),
        )
        reply = answer_data(
            answer, question, brief, self._remote.link, self._remote.kind_of
        )
        reply["server"] = {"host": self.host, "version": self._remote.version}
        return json.dumps(reply, ensure_ascii=False)

    def list_chunks(self, arguments: Mapping[str, Any]) -> str:
        """Return the definitions the server lists, as lines."""
        _no_path(arguments)
        names = ("lang", "under", "type", "format")
        return self._remote.read(
            "list_chunks",
            {name: arguments[name] for name in names if arguments.get(name)},
        )

    def index_status(self, arguments: Mapping[str, Any]) -> str:
        """Return what the index of the server holds, as JSON."""
        _no_path(arguments)
        return self._remote.read("index_status", {})

    def complete_filter(self, arguments: Mapping[str, Any]) -> str:
        """Return the filter word the server finishes, as JSON."""
        _no_path(arguments)
        return self._remote.read(
            "complete_filter", {"query": str(arguments.get("query") or "")}
        )

    def tools(self) -> list[Tool]:
        """Describe the tools that travel to a server.

        Each one reads the server, and none changes it, so a refresh
        stays with the operator of the server.
        """
        return [
            search_tool(self.search),
            ask_tool(self.ask),
            list_tool(self.list_chunks),
            status_tool(self.index_status),
            complete_tool(self.complete_filter),
        ]


def _no_path(arguments: Mapping[str, Any]) -> None:
    """Raise ``ValueError`` for a call that names a path."""
    if arguments.get("path"):
        raise ValueError(NO_PATH)


def _text(arguments: Mapping[str, Any], name: str) -> str:
    """Return the text argument *name* of a call, and refuse a path.

    Raise ``ValueError`` for an empty text and for any ``path``.
    """
    if arguments.get("path"):
        raise ValueError(NO_PATH)
    text = str(arguments.get(name) or "").strip()
    if not text:
        raise ValueError(f"The {name!r} argument is required.")
    return text
