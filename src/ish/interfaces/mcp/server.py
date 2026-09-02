"""Expose ish over the Model Context Protocol.

This interface is long-lived, which is what makes it fast: the embedding
backend and the persistent index are opened once and reused by every
call, so a query costs a search rather than a startup.
"""

import logging
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ish import bootstrap
from ish.application.search import Search
from ish.interfaces.cli.log import setup_logging
from ish.interfaces.format import format_chunk_line, format_result_line
from ish.interfaces.mcp.protocol import Server, Tool
from ish.settings import Settings, load_settings

log = logging.getLogger("ish.mcp")

SERVER_NAME = "ish"

_PATH_PROPERTY = {
    "type": "string",
    "description": (
        "Directory or file to search. Defaults to the directory the server "
        "was started in."
    ),
}

# A tool may accept only the options that narrow what a search returns.
# An option that decides what enters the index must stay out, because
# the next refresh would prune whatever a single call excluded.
_QUERY_PROPERTIES: dict[str, dict[str, Any]] = {
    "limit": {
        "type": "integer",
        "description": "How many results to return.",
        "minimum": 1,
    },
    "lang": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Return results only from these languages, such as cpp or yaml."
        ),
    },
    "under": {
        "type": "string",
        "description": (
            "Return results only from paths matching this regular expression."
        ),
    },
}


class IshTools:
    """Hold one search use case per scanned tree, so indexes stay warm."""

    def __init__(self, settings: Settings, root: Path) -> None:
        self._settings = settings
        self._root = root
        self._by_root: dict[Path, Search] = {}

    def close(self) -> None:
        """Release every index this server opened."""
        for search in self._by_root.values():
            search.close()
        self._by_root.clear()

    def _resolve(self, raw: Any) -> Path:
        """Turn a tool argument into a path that exists."""
        path = Path(str(raw)).expanduser() if raw else self._root
        path = path.resolve()
        if not path.exists():
            raise ValueError(f"Path does not exist: {path}")
        return path

    def _filter_for(self, arguments: Mapping[str, Any]):
        """Build the result filter for one call.

        Fall back to the configured value for anything the call omits.
        """
        from ish.application.search import build_result_filter

        lang = arguments.get("lang") or self._settings.lang
        under = arguments.get("under") or self._settings.under
        return build_result_filter([str(item) for item in lang], str(under))

    def _search_for(self, root: Path) -> Search:
        """Return the use case for *root*, building it on first use."""
        if root not in self._by_root:
            log.info("Opening an index for %s", root)
            self._by_root[root] = bootstrap.build_search(self._settings, root)
        return self._by_root[root]

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def search(self, arguments: Mapping[str, Any]) -> str:
        """Rank chunks against a natural-language query."""
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("The 'query' argument is required.")

        root = self._resolve(arguments.get("path"))
        limit = int(arguments.get("limit") or self._settings.limit)

        use_case = self._search_for(root)
        use_case.build_index(root)
        results = use_case.search(query, limit=limit, keep=self._filter_for(arguments))
        if not results:
            return f"No results for {query!r} under {root}."

        lines = [format_result_line(chunk, score) for chunk, score in results]
        return "\n".join(lines)

    def list_chunks(self, arguments: Mapping[str, Any]) -> str:
        """List every chunk the parsers find under a path."""
        root = self._resolve(arguments.get("path"))
        chunks = bootstrap.build_scan(self._settings, root).run(root)
        keep = self._filter_for(arguments)
        if keep is not None:
            chunks = [chunk for chunk in chunks if keep(chunk)]
        if not chunks:
            return f"No source files found under {root}."
        return "\n".join(format_chunk_line(chunk) for chunk in chunks)

    def index_status(self, arguments: Mapping[str, Any]) -> str:
        """Report what the stored index holds for a path."""
        root = self._resolve(arguments.get("path"))
        use_case = self._search_for(root)
        use_case.build_index(root)
        chunks = use_case.all_chunks()

        languages: dict[str, int] = {}
        for chunk in chunks:
            languages[chunk.language] = languages.get(chunk.language, 0) + 1
        breakdown = ", ".join(f"{n} {lang}" for lang, n in sorted(languages.items()))

        return (
            f"Index for {root}\n"
            f"  chunks   : {len(chunks)}\n"
            f"  languages: {breakdown or 'none'}\n"
            f"  embedder : {self._settings.embedder}\n"
            f"  file     : {bootstrap.index_path(self._settings, root)}"
        )

    def tools(self) -> list[Tool]:
        """Describe every tool this server offers."""
        return [
            Tool(
                name="search_code",
                description=(
                    "Search a codebase by meaning rather than by literal text. "
                    "Returns ranked chunks (functions, classes, methods, and "
                    "sections) with their file, line range, and score. Use it "
                    "for questions such as 'where is authentication handled' "
                    "or 'what computes the ranking', where the wording of the "
                    "question will not appear in the code."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to look for, in plain language.",
                        },
                        "path": _PATH_PROPERTY,
                        **_QUERY_PROPERTIES,
                    },
                    "required": ["query"],
                },
                handler=self.search,
            ),
            Tool(
                name="list_chunks",
                description=(
                    "List every definition under a path, with its kind, "
                    "qualified name, and line range. Use it to survey a file "
                    "or package without reading the source."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "path": _PATH_PROPERTY,
                        "lang": _QUERY_PROPERTIES["lang"],
                        "under": _QUERY_PROPERTIES["under"],
                    },
                },
                handler=self.list_chunks,
            ),
            Tool(
                name="index_status",
                description=(
                    "Report how many chunks the stored index holds for a path, "
                    "which languages they came from, and which embedding "
                    "backend produced them. Refreshes the index first."
                ),
                schema={
                    "type": "object",
                    "properties": {"path": _PATH_PROPERTY},
                },
                handler=self.index_status,
            ),
        ]


def _version() -> str:
    """Return the installed package version."""
    import importlib.metadata

    try:
        return importlib.metadata.version("interactive-semantic-hunt")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    """Run the MCP server on stdio. Return an exit code."""
    root = Path.cwd()
    settings = load_settings(start=root)

    # stdout carries the protocol, so every log line must go to stderr.
    setup_logging(verbosity=settings.verbosity, color=False)

    tools = IshTools(settings, root)
    server = Server(SERVER_NAME, _version(), tools.tools())
    try:
        server.serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        tools.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
