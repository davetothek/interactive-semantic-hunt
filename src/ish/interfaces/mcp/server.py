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
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ish import bootstrap
from ish.application.answer import Answer
from ish.application.categories import TYPES
from ish.application.progress import Progress
from ish.domain.chunk import Chunk
from ish.interfaces import heartbeat
from ish.interfaces.format import (
    NOT_COVERED_TEXT,
    answer_usage,
    display_path,
    format_answer,
    format_brief,
    layout_data,
    number_citations,
    render,
    symbol_of,
    terms_data,
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

    def warm_reader(self, root: Path | None = None) -> threading.Thread | None:
        """Parse the files of *root* for answers on a thread, with ``ask_warm``.

        Open the session and plan the files here, so a question that
        arrives at once meets one session. The thread only reads, through
        the reader every answer shares. Return the thread, or None when
        ``ask_warm`` is off.
        """
        if not self._settings.ask_warm:
            return None
        tree = root or self._root
        session = self._session_for(tree)
        plan = session.warm_reader_plan()

        def run() -> None:
            start = time.monotonic()
            try:
                count = session.warm_reader(plan)
            except Exception:
                log.warning("Could not parse %s for answers", tree, exc_info=True)
                return
            log.info(
                "Parsed %d files of %s for answers in %.1f s, %d acronyms defined",
                count,
                tree,
                time.monotonic() - start,
                session.glossary_size(),
            )

        thread = threading.Thread(target=run, name="ish-warm", daemon=True)
        thread.start()
        return thread

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
        results = session.search(query, limit, **narrowing_of(arguments))
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
        brief = bool(arguments.get("brief"))
        answer = session.ask(
            question,
            brief=brief,
            turns=turns_of(arguments.get("ask_turns")),
            **narrowing_of(arguments),
        )
        reply = answer_data(
            answer, question, brief, session.link, session.kind_of, base=root
        )
        return json.dumps(reply, ensure_ascii=False)

    def sources(self, arguments: Mapping[str, Any]) -> str:
        """Return the chunks a search finds with the text of each, as JSON.

        A client that writes the answer with a model of its own reads
        this in place of ``ask``. Each chunk carries what a citation
        needs and its text. Each term carries the meaning of an acronym
        the texts use, and the chunk that spells it out.
        """
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("The 'query' argument is required.")
        root = self._resolve(arguments.get("path"))
        limit = _limit(arguments.get("limit"), self._settings.limit)
        session = self._session_for(root)
        self._keep_current(root, session)
        found = session.sources(query, limit, **narrowing_of(arguments))

        def described(chunk: Chunk) -> dict[str, Any]:
            return {
                "path": str(display_path(chunk.path, root)),
                "type": session.kind_of(chunk),
                "location": chunk.location.to_dict(),
                "kind": chunk.kind,
                "symbol": chunk.symbol,
                "language": chunk.language,
                "url": session.link(chunk),
            }

        return json.dumps(
            {
                "sources": [
                    described(entry.chunk) | {"score": entry.score, "text": entry.text}
                    for entry in found.chunks
                ],
                "terms": [
                    {
                        "term": term.short,
                        "meaning": term.meaning,
                        "source": described(term.chunk),
                    }
                    for term in found.terms
                ],
            },
            ensure_ascii=False,
        )

    def list_chunks(self, arguments: Mapping[str, Any]) -> str:
        """List every chunk the parsers find under a path."""
        root = self._resolve(arguments.get("path"))
        chunks = self._session_for(root).scan(**narrowing_of(arguments))
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

    def tools(self, over_http: bool = False) -> list[Tool]:
        """Describe every tool this server offers.

        With *over_http*, offer the sources tool in place of ``ask``. A
        client on another machine reads the text of the chunks and
        answers with a model of its own. A server over HTTP sends text
        to each client it admits, so it builds no model for them.
        """
        offered = [
            search_tool(self.search),
            ask_tool(self.ask),
            list_tool(self.list_chunks),
            status_tool(self.index_status),
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
            complete_tool(self.complete_filter),
        ]
        if over_http:
            offered = [tool for tool in offered if tool.name != "ask"]
            offered.append(
                Tool(
                    name="sources",
                    description=(
                        "Return the chunks a search finds with the text of "
                        "each, for a client that writes the answer with a "
                        "model of its own. Returns JSON: 'sources' lists the "
                        "ranked chunks, each with its path, type, location, "
                        "kind, symbol, language, score, link, and text, and "
                        "'terms' gives the meaning of each acronym the texts "
                        "use, with the chunk that spells it out. The text is "
                        "empty for a chunk the server cannot read."
                    ),
                    schema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "What to look for, in plain language.",
                            },
                            "path": _PATH_PROPERTY,
                            "limit": {
                                "type": "integer",
                                "description": "How many chunks to return.",
                                "minimum": 1,
                                "maximum": MAX_SOURCES,
                            },
                            "lang": _QUERY_PROPERTIES["lang"],
                            "under": _QUERY_PROPERTIES["under"],
                            "type": _QUERY_PROPERTIES["type"],
                        },
                        "required": ["query"],
                    },
                    handler=self.sources,
                )
            )
        return offered


def search_tool(handler: Callable[[Mapping[str, Any]], str]) -> Tool:
    """Describe ``search_code``, which *handler* answers."""
    return Tool(
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
        handler=handler,
    )


def ask_tool(handler: Callable[[Mapping[str, Any]], str]) -> Tool:
    """Describe ``ask``, which *handler* answers."""
    return Tool(
        name="ask",
        description=(
            "Answer a question from the documents and code under a "
            "path, with a citation for each claim. Returns JSON: the "
            "answer with [n] markers, its layout (a short answer, "
            "sections of prose, steps, or tables, and what the "
            "documents leave open), and per citation the path, the "
            "type, the location, and a link that opens the source, and "
            "the whole answer as Markdown, and what it cost: the "
            "models, their tokens, and the seconds of each stage in "
            "'timings'. When the "
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
                "brief": {
                    "type": "boolean",
                    "description": (
                        "Answer in a sentence or two with no citations. "
                        "Ask again without it for the sources."
                    ),
                },
                "ask_turns": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_ASK_TURNS,
                    "description": (
                        "Follow-up searches the model may ask for "
                        "before it answers. Leave it out for the "
                        "server's own setting."
                    ),
                },
                "lang": _QUERY_PROPERTIES["lang"],
                "under": _QUERY_PROPERTIES["under"],
                "type": _QUERY_PROPERTIES["type"],
            },
            "required": ["question"],
        },
        handler=handler,
    )


def list_tool(handler: Callable[[Mapping[str, Any]], str]) -> Tool:
    """Describe ``list_chunks``, which *handler* answers."""
    return Tool(
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
        handler=handler,
    )


def status_tool(handler: Callable[[Mapping[str, Any]], str]) -> Tool:
    """Describe ``index_status``, which *handler* answers."""
    return Tool(
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
        handler=handler,
    )


def complete_tool(handler: Callable[[Mapping[str, Any]], str]) -> Tool:
    """Describe ``complete_filter``, which *handler* answers."""
    return Tool(
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
        handler=handler,
    )


def narrowing_of(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return the query-scope filters a call passed, as keyword arguments."""
    return {
        "lang": tuple(str(item) for item in arguments.get("lang") or ()),
        "under": str(arguments.get("under") or ""),
        "type": tuple(str(item) for item in arguments.get("type") or ()),
    }


def answer_data(
    answer: Answer,
    question: str,
    brief: bool,
    link: Callable[[Chunk], str],
    kind_of: Callable[[Chunk], str],
    *,
    base: Path | None = None,
) -> dict[str, Any]:
    """Return the reply of ``ask`` for *answer*, as data for JSON.

    Give the answer with numbered citations, and for each citation the
    path from *base*, the location as data, and a link. A question the
    documents do not cover gets the closest sources in place of an
    answer.
    """

    def described(chunk: Chunk, number: int | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {} if number is None else {"n": number}
        entry |= {
            "path": str(display_path(chunk.path, base)),
            "type": kind_of(chunk),
            "location": chunk.location.to_dict(),
            "kind": chunk.kind,
            "symbol": symbol_of(chunk),
            "url": link(chunk),
        }
        return entry

    # What the answer cost: the models, their tokens, and the seconds
    # of each stage, so a client can show where the time went.
    reply: dict[str, Any] = {
        "covered": answer.covered,
        "dropped": answer.dropped,
        **answer_usage(answer),
    }
    if brief:
        reply["answer"] = format_brief(answer)
        return reply
    if answer.covered:
        text, cited = number_citations(answer)
        layout, _ = layout_data(answer)
        terms, cited = terms_data(answer, cited)
        reply["answer"] = text
        reply["layout"] = layout
        reply["terms"] = terms
        reply["citations"] = [described(c, n) for n, c in enumerate(cited, 1)]
    else:
        reply["answer"] = NOT_COVERED_TEXT
        reply["sources"] = [described(c) for c in answer.sources]
    # The text `ish --ask` prints, so a client in a terminal shows
    # the answer the way a local run does.
    reply["markdown"] = format_answer(
        answer, question, link, base=base, kind_of=kind_of
    )
    return reply


# The most follow-up turns one call may ask for. Each turn is one more
# call to the model, which a client pays for in seconds and the server in
# tokens.
MAX_ASK_TURNS = 5


def turns_of(value: Any) -> int | None:
    """Return the turns a call asked for, or None for the server's setting.

    Raise ``ValueError`` for a value that is not a count from 0 to
    ``MAX_ASK_TURNS``, so the call fails rather than a bound being lifted.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("'ask_turns' must be a whole number.")
    if not 0 <= value <= MAX_ASK_TURNS:
        raise ValueError(f"'ask_turns' must be from 0 to {MAX_ASK_TURNS}.")
    return value


# The most chunks one call of the sources tool may return. Each chunk
# travels with its text, up to the cap a parser sets, so the bound keeps
# one reply to a few megabytes.
MAX_SOURCES = 200


def _limit(value: Any, default: int) -> int:
    """Return the chunks a sources call asked for, or *default* for none.

    Raise ``ValueError`` for a value that is not a count from 1 to
    ``MAX_SOURCES``, so the call fails rather than a bound being lifted.
    """
    if value is None:
        return min(default, MAX_SOURCES)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("'limit' must be a whole number.")
    if not 1 <= value <= MAX_SOURCES:
        raise ValueError(f"'limit' must be from 1 to {MAX_SOURCES}.")
    return value


def installed_version() -> str:
    """Return the installed package version."""
    import importlib.metadata

    try:
        return importlib.metadata.version("interactive-semantic-hunt")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _parse(argv: list[str] | None, prog: str):
    import argparse

    parser = argparse.ArgumentParser(
        prog=prog,
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


def _warn_of_a_tree_that_sets_sign_in(root: Path) -> None:
    """Name each file in the tree that sets how the server signs users in.

    The loader ignores those options in a file of the tree, because a
    person who can commit to the tree would choose the directory that gets
    each password.
    """
    from ish.settings import tree_files_setting, user_config_path

    for path, names in tree_files_setting(root):
        kept = [name for name in names if name != "remote"]
        if kept:
            log.warning(
                "Ignore %s in %s. A file in a tree does not set how a server "
                "signs users in. Set them in %s or in ISH_* variables.",
                ", ".join(kept),
                path,
                user_config_path(),
            )


def _refuse_a_password_from_the_network(host: str, methods: list[str]) -> None:
    """Raise ``ValueError`` when a server that signs users in listens on a network.

    ish holds no certificate. A password reaches a server that signs
    users in through a reverse proxy on this machine that holds one, so
    the server listens on the loopback address only.
    """
    import ipaddress

    if not methods or host == "localhost":
        return
    try:
        if ipaddress.ip_address(host.strip("[]")).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError(
        f"A server that signs users in with {', '.join(methods)} listens on this "
        "machine only, behind a reverse proxy that holds the certificate. "
        "Give --http 127.0.0.1:PORT."
    )


def _address(text: str) -> tuple[str, int]:
    """Split HOST:PORT, and read a bare PORT as one on the loopback address."""
    host, _, port = text.rpartition(":")
    if not port.isdigit():
        raise ValueError(f"Expected HOST:PORT for --http, got {text!r}.")
    return host or "127.0.0.1", int(port)


def main(argv: list[str] | None = None, prog: str = "ish-mcp") -> int:
    """Run the MCP server on stdio, or over HTTP with ``--http``.

    Return an exit code. *prog* is the name the usage line shows, because
    ``ish mcp`` starts this server as well.
    """
    args = _parse(argv, prog)
    root = Path.cwd()
    settings = load_settings(start=root)

    # stdout carries the protocol, so every log line must go to stderr.
    setup_logging(verbosity=settings.verbosity, color=False)

    if args.http:
        _warn_of_a_tree_that_sets_sign_in(root)
        try:
            address = _address(args.http)
            access = bootstrap.build_access(settings)
            _refuse_a_password_from_the_network(address[0], access.methods())
            transport = HttpTransport(*address, access.admit, access)
        except ValueError as exc:
            log.error("%s", exc)
            return 1

    if args.http:
        # Who called which tool shows at every verbosity.
        logging.getLogger("ish.audit").setLevel(logging.INFO)
    tools = IshTools(settings, root)
    server = Server(
        SERVER_NAME, installed_version(), tools.tools(over_http=bool(args.http))
    )
    try:
        tools.warm_reader()
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
