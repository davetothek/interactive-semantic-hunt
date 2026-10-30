"""Expose ish over the Model Context Protocol.

This interface is long-lived, which is what makes it fast: the embedding
backend and the persistent index are opened once and reused by every
call, so a query costs a search rather than a startup.

The server refreshes each tree it is asked about on a thread of its
own, unless ``ish-serve`` keeps the tree current. Then it reads what
that process writes, and passes a request for a refresh on to it.
"""

import json
import logging
import os
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ish import bootstrap
from ish.application.categories import TYPES
from ish.application.progress import Progress
from ish.domain.chunk import Chunk
from ish.interfaces import heartbeat
from ish.interfaces.format import (
    NOT_COVERED_TEXT,
    display_path,
    number_citations,
    render,
    symbol_of,
)
from ish.interfaces.log import setup_logging
from ish.interfaces.mcp.protocol import Server, Tool
from ish.interfaces.mcp.transport import TOKEN_VARIABLE, HttpTransport
from ish.interfaces.python.api import Ish
from ish.settings import Settings, choices_of, load_settings

log = logging.getLogger("ish.mcp")

# How long closing waits for a watch to finish what it is doing.
# Long enough for a refresh between two checks of the flag, short
# enough that nobody notices.
WATCH_STOP_SECONDS = 5.0

SERVER_NAME = "ish"

# How a search that found nothing begins its answer. A client that
# prints results as lines reads this as prose, not as a result.
NO_RESULTS = "No results for"

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
        "items": {"type": "string", "enum": list(TYPES)},
        "description": (
            "Return results only of these kinds. Use doc for prose, test "
            "for tests and fixtures, config for YAML and JSON settings."
        ),
    },
    "format": {
        "type": "string",
        "enum": list(choices_of("format")),
        "description": (
            "Shape of each line. Use grep for an editor, which opens a "
            "result at path:line:column."
        ),
    },
}


class IshTools:
    """Hold one session per scanned tree, so indexes stay warm."""

    def __init__(self, settings: Settings, root: Path) -> None:
        self._settings = settings
        self._root = root
        self._by_root: dict[Path, Ish] = {}
        # Trees a thread is already keeping current, and what each is
        # doing, so a caller can say so while it reads stale answers.
        self._watched: dict[Path, threading.Event] = {}
        # The threads themselves, so closing can wait for them.
        self._watchers: list[threading.Thread] = []
        self._progress: dict[Path, str] = {}
        # What each index holds, counted again only when it changed.
        self._status: dict[Path, tuple[tuple[int, float | None], dict[str, Any]]] = {}
        self._closing = threading.Event()
        # The host's calls and the watch threads reach these at once,
        # and a tree must get one session and one watch however many ask.
        self._lock = threading.Lock()

    def close(self) -> None:
        """Release every index this server opened, and stop watching.

        Wait briefly for each watch to notice. A thread that outlives
        this call would go on writing to wherever it last resolved the
        index directory to be, which in a test is a directory that has
        already been taken away.
        """
        self._closing.set()
        for wake in self._watched.values():
            wake.set()
        for watcher in self._watchers:
            watcher.join(timeout=WATCH_STOP_SECONDS)
        self._watchers.clear()
        self._watched.clear()
        for session in self._by_root.values():
            session.close()
        self._by_root.clear()

    def _resolve(self, raw: Any) -> Path:
        """Turn a tool argument into a path that exists."""
        path = Path(str(raw)).expanduser() if raw else self._root
        path = path.resolve()
        if not path.exists():
            raise ValueError(f"Path does not exist: {path}")
        return path

    @staticmethod
    def _narrowing(arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Return the query-scope filters a call passed, as keyword arguments."""
        return {
            "lang": tuple(str(item) for item in arguments.get("lang") or ()),
            "under": str(arguments.get("under") or ""),
            "type": tuple(str(item) for item in arguments.get("type") or ()),
        }

    def _keep_current(self, root: Path, session: Ish) -> None:
        """Keep the index for *root* current behind the questions.

        The server outlives the files it describes, and an editor asks
        about code it is in the middle of changing. Refreshing on the
        way to an answer would put a walk of the tree in front of every
        keystroke, so refresh on a thread instead and let a search read
        whatever is there. The stored matrix notices another connection
        committing, so a search never serves what a refresh replaced.

        A parent read from the indexes below it is not writable, and
        refreshing through it did nothing at all. Refresh each tree
        beneath it, which is what brings an edit into view.

        Start nothing while ``ish-serve`` keeps the tree current. A
        thread here would walk and embed the same files beside it.
        """
        if session.kept_by() is not None:
            return
        with self._lock:
            if root in self._watched:
                return
            wake = threading.Event()
            self._watched[root] = wake
            watcher = threading.Thread(
                target=self._refresh_forever,
                args=(root, wake),
                name=f"ish-refresh-{root.name}",
                daemon=True,
            )
            self._watchers.append(watcher)
            watcher.start()

    def watch(self, root: Path) -> None:
        """Keep *root* current from now on, and look at it right away."""
        self._keep_current(root, self._session_for(root))
        wake = self._watched.get(root)
        if wake is not None:
            wake.set()

    def _refresh_forever(self, root: Path, wake: threading.Event) -> None:
        """Bring every index under *root* up to date, when asked or in time.

        Leave a cycle to ``ish-serve`` when one has started keeping the
        tree current since the watch began.
        """
        while not self._closing.is_set():
            # Wake early when something asks, otherwise on the interval.
            wake.wait(self._settings.refresh_seconds or None)
            wake.clear()
            if self._closing.is_set():
                return
            if self._kept(root):
                log.info(
                    "ish-serve keeps %s current now. Leaving it the refresh.", root
                )
                continue
            self._refresh_once(root)

    def _kept(self, root: Path) -> bool:
        """Return True when ``ish-serve`` keeps *root* current."""
        session = self._by_root.get(root)
        return session is not None and session.kept_by() is not None

    def _refresh_once(self, root: Path) -> None:
        """Refresh *root*, recording what it is doing as it goes."""
        self._progress[root] = "starting"

        def note(step: Progress) -> None:
            self._progress[root] = str(step)

        try:
            bootstrap.refresh_indexes(self._settings, root, on_progress=note)
            # The watch thread has nothing waiting on it, so pay the
            # model load here rather than on the next query.
            session = self._by_root.get(root)
            if session is not None:
                session.warm()
        except Exception as exc:  # noqa: BLE001 - a watch must not die
            log.warning("Cannot refresh %s: %s", root, exc)
        finally:
            self._progress.pop(root, None)
            self._status.pop(root, None)

    def refresh(self, arguments: Mapping[str, Any]) -> str:
        """Ask for a refresh now, and return without waiting for it.

        An editor opens a picker over code it has been changing, so this
        is the moment to look, and the answers must keep coming while it
        does. Report progress through ``index_status``. Pass the request
        on to ``ish-serve`` when one keeps the tree current.
        """
        root = self._resolve(arguments.get("path"))
        kept = self._session_for(root).kept_by()
        if kept is not None:
            heartbeat.nudge(kept.tree)
            return f"Asked ish-serve to refresh {kept.tree} in the background."
        self.watch(root)
        return f"Refreshing {root} in the background."

    def _session_for(self, root: Path) -> Ish:
        """Return the session for *root*, opening it on first use."""
        with self._lock:
            if root not in self._by_root:
                log.info("Opening an index for %s", root)
                self._by_root[root] = Ish(root, settings=self._settings)
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

        session = self._session_for(root)
        self._keep_current(root, session)
        # The session reads `lang:cpp type:doc` out of the query itself,
        # so an editor can pass the line as typed.
        results = session.search(query, limit, **self._narrowing(arguments))
        if not results:
            return f"{NO_RESULTS} {query!r} under {root}."

        # Write each path from the directory asked about. A caller in
        # another directory resolves it against that one, and the
        # working directory of a shared server means nothing to it.
        shape = str(arguments.get("format") or self._settings.format)
        return "\n".join(
            render(chunk, shape, score, base=root) for chunk, score in results
        )

    def ask(self, arguments: Mapping[str, Any]) -> str:
        """Answer a question from the chunks a search finds, as JSON.

        Return the answer with numbered citations, and for each citation
        the path from the directory asked about, the location as data,
        and a link. A question the documents do not cover returns the
        closest sources instead of an answer.
        """
        question = str(arguments.get("question") or "").strip()
        if not question:
            raise ValueError("The 'question' argument is required.")
        root = self._resolve(arguments.get("path"))
        session = self._session_for(root)
        self._keep_current(root, session)
        answer = session.ask(question, **self._narrowing(arguments))

        def described(chunk: Chunk, number: int | None = None) -> dict[str, Any]:
            entry: dict[str, Any] = {} if number is None else {"n": number}
            entry |= {
                "path": str(display_path(chunk.path, root)),
                "location": chunk.location.to_dict(),
                "kind": chunk.kind,
                "symbol": symbol_of(chunk),
                "url": session.link(chunk),
            }
            return entry

        reply: dict[str, Any] = {
            "covered": answer.covered,
            "model": answer.model,
            "input_tokens": answer.input_tokens,
            "output_tokens": answer.output_tokens,
            "dropped": answer.dropped,
        }
        if answer.covered:
            text, cited = number_citations(answer)
            reply["answer"] = text
            reply["citations"] = [described(c, n) for n, c in enumerate(cited, 1)]
        else:
            reply["answer"] = NOT_COVERED_TEXT
            reply["sources"] = [described(c) for c in answer.sources]
        return json.dumps(reply, ensure_ascii=False)

    def list_chunks(self, arguments: Mapping[str, Any]) -> str:
        """List every chunk the parsers find under a path."""
        root = self._resolve(arguments.get("path"))
        chunks = self._session_for(root).scan(**self._narrowing(arguments))
        if not chunks:
            return f"No source files found under {root}."
        shape = str(arguments.get("format") or self._settings.format)
        return "\n".join(render(chunk, shape, base=root) for chunk in chunks)

    def index_status(self, arguments: Mapping[str, Any]) -> str:
        """Report what the stored index holds for a path.

        Say what ``ish-serve`` is doing when one keeps the tree, and
        what the watch thread is doing otherwise.
        """
        root = self._resolve(arguments.get("path"))
        session = self._session_for(root)
        self._keep_current(root, session)
        kept = session.kept_by()
        status = self._status_of(root, session, kept)

        languages: Any = status["languages"]
        breakdown = ", ".join(f"{n} {lang}" for lang, n in languages.items())

        doing = self._progress.get(root) if kept is None else kept.refreshing
        keeper = (
            ""
            if kept is None
            else f"  kept by  : ish-serve {kept.pid} for {kept.tree}\n"
        )
        return (
            f"Index for {root}\n"
            f"  refreshing: {doing or 'no'}\n"
            f"{keeper}"
            f"  chunks   : {status['chunks']}\n"
            f"  files    : {status['files']} with chunks, "
            f"{status['empty_files']} with none\n"
            f"  languages: {breakdown or 'none'}\n"
            f"  embedder : {self._settings.embedder}\n"
            f"  file     : {bootstrap.catalog(self._settings).path_for(root)}"
        )

    def _status_of(
        self, root: Path, session: Ish, kept: heartbeat.Status | None
    ) -> dict[str, Any]:
        """Return what the index holds, counted again only when it changed.

        Counting by language builds every chunk, which on a root over
        73,000 chunks costs about a second, and a picker asks while it
        waits. The count of chunks, and the time ``ish-serve`` last
        finished, tell a change from a repeat.
        """
        key = (session.held(), None if kept is None else kept.last_finished)
        cached = self._status.get(root)
        if cached is None or cached[0] != key:
            cached = (key, session.status())
            self._status[root] = cached
        return cached[1]

    def complete_filter(self, arguments: Mapping[str, Any]) -> str:
        """Finish the filter word a query ends in, for an editor's picker.

        Return a JSON object. ``text`` is the query with its last word
        grown as far as the choices agree, unchanged when nothing fits.
        ``candidates`` names the choices when the word could still
        become several things, and is empty otherwise. An editor reads
        both in one call, so a Tab costs a round trip and no process
        start.
        """
        query = str(arguments.get("query") or "")
        session = self._session_for(self._resolve(arguments.get("path")))
        return json.dumps(
            {"text": session.complete(query), "candidates": session.candidates(query)}
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
                name="ask",
                description=(
                    "Answer a question from the documents and code under a "
                    "path, with a citation for each claim. Returns JSON: the "
                    "answer with [n] markers, and per citation the path, the "
                    "location, and a link that opens the source. When the "
                    "documents do not answer the question, 'covered' is "
                    "false and 'sources' lists the closest chunks. Use it "
                    "for questions such as 'how is the watchdog configured'."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question, in plain language.",
                        },
                        "path": _PATH_PROPERTY,
                        "lang": _QUERY_PROPERTIES["lang"],
                        "under": _QUERY_PROPERTIES["under"],
                        "type": _QUERY_PROPERTIES["type"],
                    },
                    "required": ["question"],
                },
                handler=self.ask,
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
            Tool(
                name="complete_filter",
                description=(
                    "Finish the filter word a query ends in, the way a shell "
                    "finishes a path: ty becomes type:, lang:cp becomes "
                    "lang:cpp, and under:/s becomes the subtree that starts "
                    "with s. Returns JSON with the completed text and, when "
                    "several answers fit, the candidates to choose between. "
                    "For an editor's picker, which calls it on Tab."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The query as typed so far.",
                        },
                        "path": _PATH_PROPERTY,
                    },
                    "required": ["query"],
                },
                handler=self.complete_filter,
            ),
        ]


def installed_version() -> str:
    """Return the installed package version."""
    import importlib.metadata

    try:
        return importlib.metadata.version("interactive-semantic-hunt")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _parse(argv: list[str] | None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="ish-mcp",
        description="Serve the ish tools over MCP, on stdio unless told otherwise.",
    )
    parser.add_argument(
        "--http",
        metavar="HOST:PORT",
        help=(
            "Serve MCP over HTTP at /mcp on HOST:PORT instead of stdio. "
            f"Every request must carry ${TOKEN_VARIABLE} as a bearer token."
        ),
    )
    return parser.parse_args(argv)


def _address(text: str) -> tuple[str, int]:
    """Split HOST:PORT, and read a bare PORT as one on the loopback address."""
    host, _, port = text.rpartition(":")
    if not port.isdigit():
        raise ValueError(f"Expected HOST:PORT for --http, got {text!r}.")
    return host or "127.0.0.1", int(port)


def main(argv: list[str] | None = None) -> int:
    """Run the MCP server on stdio, or over HTTP with ``--http``.

    Return an exit code.
    """
    args = _parse(argv)
    root = Path.cwd()
    settings = load_settings(start=root)

    # stdout carries the protocol, so every log line must go to stderr.
    setup_logging(verbosity=settings.verbosity, color=False)

    if args.http:
        try:
            transport = HttpTransport(
                *_address(args.http), os.environ.get(TOKEN_VARIABLE, "")
            )
        except ValueError as exc:
            log.error("%s", exc)
            return 1

    tools = IshTools(settings, root)
    server = Server(SERVER_NAME, installed_version(), tools.tools())
    try:
        if args.http:
            server.serve_on(transport)
        else:
            server.serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        tools.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
