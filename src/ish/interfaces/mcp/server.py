"""Expose ish over the Model Context Protocol.

This interface is long-lived, which is what makes it fast: the embedding
backend and the persistent index are opened once and reused by every
call, so a query costs a search rather than a startup.
"""

import logging
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ish import bootstrap
from ish.application.search import (
    Filters,
    Search,
    parse_query,
)
from ish.interfaces.cli.log import setup_logging
from ish.interfaces.format import (
    format_chunk_line,
    format_grep_line,
    format_result_line,
)
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
    "type": {
        "type": "array",
        "items": {"type": "string", "enum": ["code", "doc", "test", "config"]},
        "description": (
            "Return results only of these kinds. Use doc for prose, test "
            "for tests and fixtures, config for YAML and JSON settings."
        ),
    },
    "format": {
        "type": "string",
        "enum": ["plain", "grep"],
        "description": (
            "Shape of each line. Use grep for an editor, which opens a "
            "result at path:line:column."
        ),
    },
}


class IshTools:
    """Hold one search use case per scanned tree, so indexes stay warm."""

    def __init__(self, settings: Settings, root: Path) -> None:
        self._settings = settings
        self._root = root
        self._by_root: dict[Path, Search] = {}
        # Trees a thread is already keeping current, and what each is
        # doing, so a caller can say so while it reads stale answers.
        self._watched: dict[Path, threading.Event] = {}
        self._progress: dict[Path, str] = {}
        self._closing = threading.Event()

    def close(self) -> None:
        """Release every index this server opened."""
        self._closing.set()
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

    def _filter_for(self, arguments: Mapping[str, Any], typed: Filters | None = None):
        """Build the result filter for one call.

        Rank a filter typed into the query above the call argument, and
        that above the configured value.
        """
        asked = Filters(
            lang=tuple(str(item) for item in arguments.get("lang") or ()),
            under=str(arguments.get("under") or ""),
            type=tuple(str(item) for item in arguments.get("type") or ()),
        )
        chain = asked.or_else(bootstrap.settings_filters(self._settings))
        return bootstrap.build_result_filter(
            self._settings, typed.or_else(chain) if typed else chain
        )

    def _keep_current(self, root: Path, use_case: Search) -> None:
        """Open the index, then keep it current behind the questions.

        The server outlives the files it describes, and an editor asks
        about code it is in the middle of changing. Refreshing on the
        way to an answer would put a walk of the tree in front of every
        keystroke, so refresh on a thread instead and let a search read
        whatever is there. The stored matrix notices another connection
        committing, so a search never serves what a refresh replaced.

        A parent read from the indexes below it is not writable, and
        refreshing through it did nothing at all. Refresh each tree
        beneath it, which is what brings an edit into view.
        """
        if root in self._watched:
            return
        wake = threading.Event()
        self._watched[root] = wake
        use_case.build_index(root)
        threading.Thread(
            target=self._refresh_forever,
            args=(root, wake),
            name=f"ish-refresh-{root.name}",
            daemon=True,
        ).start()

    def _refresh_forever(self, root: Path, wake: threading.Event) -> None:
        """Bring every index under *root* up to date, when asked or in time."""
        while not self._closing.is_set():
            # Wake early when something asks, otherwise on the interval.
            wake.wait(self._settings.refresh_seconds or None)
            wake.clear()
            if self._closing.is_set():
                return
            self._refresh_once(root)

    def _refresh_once(self, root: Path) -> None:
        """Refresh *root*, recording what it is doing as it goes."""
        self._progress[root] = "starting"
        tree = ""

        def note(message: str) -> None:
            """Keep the tree being visited beside what it is doing.

            A count of files says nothing about which tree they are in,
            and a refresh walks several.
            """
            nonlocal tree
            if message.startswith("Refreshing "):
                tree = message
                self._progress[root] = message
            else:
                self._progress[root] = f"{tree}, {message}" if tree else message

        try:
            bootstrap.refresh_indexes(self._settings, root, on_progress=note)
        except Exception as exc:  # noqa: BLE001 - a watch must not die
            log.warning("Cannot refresh %s: %s", root, exc)
        finally:
            self._progress.pop(root, None)

    def refresh(self, arguments: Mapping[str, Any]) -> str:
        """Ask for a refresh now, and return without waiting for it.

        An editor opens a picker over code it has been changing, so this
        is the moment to look, and the answers must keep coming while it
        does. Report progress through ``index_status``.
        """
        root = self._resolve(arguments.get("path"))
        use_case = self._search_for(root)
        self._keep_current(root, use_case)
        waiting = self._watched.get(root)
        if waiting is not None:
            waiting.set()
        return f"Refreshing {root} in the background."

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
        # Accept `lang:cpp type:doc` inside the query, so an editor can
        # pass the line as typed instead of parsing it first.
        query, typed = parse_query(query)
        if not query:
            raise ValueError("The 'query' argument holds only filters.")

        root = self._resolve(arguments.get("path"))
        limit = int(arguments.get("limit") or self._settings.limit)

        use_case = self._search_for(root)
        self._keep_current(root, use_case)
        keep = self._filter_for(arguments, typed)
        results = use_case.search(query, limit=limit, keep=keep)
        if not results:
            return f"No results for {query!r} under {root}."

        shape = str(arguments.get("format") or self._settings.format)
        if shape == "grep":
            lines = [format_grep_line(chunk, score) for chunk, score in results]
        else:
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
        shape = str(arguments.get("format") or self._settings.format)
        render = format_grep_line if shape == "grep" else format_chunk_line
        return "\n".join(render(chunk) for chunk in chunks)

    def index_status(self, arguments: Mapping[str, Any]) -> str:
        """Report what the stored index holds for a path."""
        root = self._resolve(arguments.get("path"))
        use_case = self._search_for(root)
        self._keep_current(root, use_case)
        chunks = use_case.all_chunks()

        languages: dict[str, int] = {}
        for chunk in chunks:
            languages[chunk.language] = languages.get(chunk.language, 0) + 1
        breakdown = ", ".join(f"{n} {lang}" for lang, n in sorted(languages.items()))

        doing = self._progress.get(root)
        return (
            f"Index for {root}\n"
            f"  refreshing: {doing or 'no'}\n"
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
                        "type": _QUERY_PROPERTIES["type"],
                        "format": _QUERY_PROPERTIES["format"],
                    },
                },
                handler=self.list_chunks,
            ),
            Tool(
                name="index_status",
                description=(
                    "Report how many chunks the stored index holds for a path, "
                    "which languages they came from, which embedding backend "
                    "produced them, and whether a refresh is running."
                ),
                schema={
                    "type": "object",
                    "properties": {"path": _PATH_PROPERTY},
                },
                handler=self.index_status,
            ),
            Tool(
                name="refresh_index",
                description=(
                    "Bring the index for a path up to date, in the background. "
                    "Returns at once; searching keeps working while it runs, "
                    "and answers improve as it goes. Call it when opening a "
                    "search over code that has been edited. Watch it finish "
                    "with index_status."
                ),
                schema={
                    "type": "object",
                    "properties": {"path": _PATH_PROPERTY},
                },
                handler=self.refresh,
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
