"""Read the chunks of an ish MCP server over HTTP.

The server searches its own tree. ``search_code`` gives the result lines
as the server writes them, and ``sources`` gives the ranked chunks with
their text and the acronyms the tree spells out. The server runs no
language model for either call.

A chunk from a server holds the path as the server writes it, from the
directory the server searches. No file of this machine is read through
it: the text, the link, and the type of each chunk come from the server.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ish.adapters.chunk_source._client import TIMEOUT_SECONDS, RemoteClient
from ish.application.glossary import Definition
from ish.application.ports.chunk_source import READ_TOOLS, ChunkSourceError
from ish.application.sources import ChunkText, Sources
from ish.domain.chunk import Chunk

# How `search_code` starts its answer when nothing matches.
NO_RESULTS = "No results for"

# What a server answers when it has no tool of the name asked for.
UNKNOWN_TOOL = "Unknown tool:"


class McpChunkSource:
    """Search one MCP server, and read the chunks it finds."""

    def __init__(
        self,
        url: str,
        token: str,
        token_variable: str,
        timeout: float = TIMEOUT_SECONDS,
        *,
        login: bool = False,
    ) -> None:
        """Read from the server at *url*, behind *token*.

        *token_variable* is the environment variable a token comes from,
        which an error about the token names. With *login*, the token is
        the login token of ``ish remote login``. Raise ``ChunkSourceError``
        for a URL that is not http or https, and for an empty token.
        """
        self._client = RemoteClient(url, token, token_variable, timeout, login=login)
        self.host = self._client.host
        self._links: dict[Chunk, str] = {}
        self._types: dict[Chunk, str] = {}

    @property
    def version(self) -> str:
        """Return the version the server reported when the session opened."""
        return self._client.server_version

    def search(
        self,
        query: str,
        limit: int,
        shape: str,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
    ) -> str:
        """Return the lines ``search_code`` answers with, or an empty text."""
        arguments = {"query": query, "limit": limit, "format": shape}
        text = self._client.call(
            "search_code", arguments | _narrowing(lang, under, type)
        )
        nothing = f"{NO_RESULTS} {query.strip()!r} under "
        return "" if text.startswith(nothing) else text

    def read(self, tool: str, arguments: Mapping[str, Any]) -> str:
        """Return the text that the read-only *tool* of the server answers with."""
        if tool not in READ_TOOLS:
            raise ChunkSourceError(f"{tool!r} is not a tool that reads the server.")
        return self._client.call(tool, dict(arguments))

    def sources(
        self,
        query: str,
        limit: int,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
    ) -> Sources:
        """Return what the ``sources`` tool answers with, as chunks and terms.

        Raise ``ChunkSourceError`` for a server that has no such tool, and
        for an answer this client cannot read.
        """
        arguments = {"query": query, "limit": limit}
        try:
            text = self._client.call(
                "sources", arguments | _narrowing(lang, under, type)
            )
        except ChunkSourceError as exc:
            if str(exc).startswith(UNKNOWN_TOOL):
                raise ChunkSourceError(
                    f"{self.host} has no sources tool. Update ish on the server."
                ) from exc
            raise
        try:
            reply = json.loads(text)
            chunks = tuple(
                ChunkText(self._chunk(entry), float(entry["score"]), str(entry["text"]))
                for entry in reply["sources"]
            )
            terms = tuple(
                Definition(
                    str(term["term"]), str(term["meaning"]), self._chunk(term["source"])
                )
                for term in reply["terms"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ChunkSourceError(
                f"{self.host} answered the sources tool in a shape this ish "
                "cannot read. Install the same ish release on both machines."
            ) from exc
        return Sources(chunks, terms)

    def link(self, chunk: Chunk) -> str:
        """Return the URL the server gave for *chunk*, or an empty text."""
        return self._links.get(chunk, "")

    def kind_of(self, chunk: Chunk) -> str:
        """Return the type the server gave for *chunk*, or an empty text."""
        return self._types.get(chunk, "")

    def _chunk(self, entry: Mapping[str, Any]) -> Chunk:
        """Return the chunk *entry* describes, and keep its link and type.

        The text stays empty. A chunk is the place of a text, and the
        text travels beside it.
        """
        location = entry["location"]
        symbol = entry["symbol"]
        chunk = Chunk(
            path=Path(str(entry["path"])),
            text="",
            kind=str(entry["kind"]),
            language=str(entry["language"]),
            symbol=None if symbol is None else str(symbol),
            start_line=int(location["start"]),
            end_line=int(location["end"]),
        )
        self._links[chunk] = str(entry["url"])
        self._types[chunk] = str(entry["type"])
        return chunk


def _narrowing(lang: Sequence[str], under: str, type: Sequence[str]) -> dict[str, Any]:
    """Return the filters that are set, as tool arguments."""
    filters: dict[str, Any] = {}
    if lang:
        filters["lang"] = list(lang)
    if under:
        filters["under"] = under
    if type:
        filters["type"] = list(type)
    return filters
