"""Interactive Textual UI for Semantic Search."""

import asyncio
import queue
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from rich.syntax import Syntax
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from ish.application.preview import load_text
from ish.application.search import (
    Filters,
    Search,
    build_result_filter,
    parse_query,
)
from ish.domain.chunk import Chunk
from ish.interfaces.format import format_selection, symbol_of


class _DaemonWorker:
    """Run one call at a time on a thread that exit never waits for.

    A thread pool registers an exit hook that joins its threads however
    it is shut down, so a request in flight held the whole process open.
    """

    def __init__(self, name: str) -> None:
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._serve, name=name, daemon=True)
        self._thread.start()

    def submit(self, loop, function, *arguments):
        """Return a future for *function*, run on this worker's thread."""
        future = loop.create_future()
        self._queue.put((loop, future, function, arguments))
        return future

    def stop(self) -> None:
        """Ask the thread to finish. It is a daemon, so exit need not wait."""
        self._queue.put(None)

    def _serve(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            loop, future, function, arguments = item
            try:
                result = function(*arguments)
            except BaseException as exc:  # noqa: BLE001 - carried to the caller
                loop.call_soon_threadsafe(_settle, future.set_exception, exc)
            else:
                loop.call_soon_threadsafe(_settle, future.set_result, result)


def _settle(setter, value) -> None:
    """Complete a future unless the caller stopped waiting."""
    try:
        setter(value)
    except asyncio.InvalidStateError:
        pass


class IshApp(App[tuple[Chunk, float] | None]):
    """Textual application for interactive semantic search."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main-container {
        height: 1fr;
        layout: horizontal;
    }
    #results-list {
        width: 40%;
        height: 1fr;
        border: solid $accent;
    }
    #preview-pane {
        width: 60%;
        height: 1fr;
        border: solid $primary;
        padding: 1;
        overflow-y: scroll;
    }
    Input {
        dock: bottom;
    }
    """

    # Textual binds ctrl+p to its command palette, which shadows the
    # fzf-style "previous result" key and adds nothing to a picker.
    ENABLE_COMMAND_PALETTE = False

    # Keep the query field focused while these keys drive the result list,
    # so the user never has to leave the input to choose a result.
    BINDINGS = [
        ("escape", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("down", "move(1)", "Next"),
        ("up", "move(-1)", "Previous"),
        ("ctrl+n", "move(1)", "Next"),
        ("ctrl+p", "move(-1)", "Previous"),
    ]

    def __init__(
        self,
        search_use_case: Search,
        root_path: Path,
        *,
        limit: int = 50,
        debounce_ms: int = 120,
        filters: Filters | None = None,
        categorize: Callable[[Chunk], str] | None = None,
    ) -> None:
        super().__init__()
        self.search_use_case = search_use_case
        self.root_path = root_path
        self.limit = limit
        self.debounce = debounce_ms / 1000
        # What the command line already narrowed. A filter typed into
        # the query line overrides it for as long as it is typed.
        self._base_filters = filters or Filters()
        # How a chunk is sorted into a type, which a repository may
        # define for itself.
        self._categorize = categorize
        # Which search is the current one. A cancelled task cannot stop
        # the thread it already handed work to, so the thread asks.
        self._generation = 0
        # Set when the interface closes, so a thread stops talking to it.
        self._leaving = threading.Event()
        # Search on one daemon thread. The embedding backend serves one
        # request at a time anyway, so running several only makes the
        # newest query wait behind queries nobody wants any more, and
        # queued work finds itself out of date and returns. A daemon
        # thread is not joined at exit, so a search in flight cannot
        # hold the interface open: a pool waited for the whole request,
        # which is why quitting during an embed appeared to hang.
        self._searcher = _DaemonWorker("ish-search")
        self._current_results: list[tuple[Chunk, float]] = []
        self._all_chunks: list[Chunk] = []

    def on_unmount(self) -> None:
        """Let the worker threads go when the interface closes."""
        self._searcher.stop()
        self._leaving.set()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with Horizontal(id="main-container"):
            yield OptionList(id="results-list")
            yield Static("Opening the index...", id="preview-pane")
        yield Input(
            placeholder="Search, or narrow with lang:cpp type:doc under:/src/",
            id="search-input",
            disabled=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        """Start the background indexing task when UI mounts."""
        self.build_index()

    def build_index(self) -> None:
        """Scan and embed the directory on a thread that never blocks exit."""
        threading.Thread(
            target=self._build_index, name="ish-index", daemon=True
        ).start()

    def _build_index(self) -> None:
        """Scan and embed, reporting progress into the preview pane."""
        try:
            chunks = self.search_use_case.build_index(
                self.root_path, self._report_progress
            )
            self._all_chunks = list(chunks) if chunks else []
            if self._still_here():
                self.call_from_thread(self._on_index_ready)
        except Exception as e:
            if self._still_here():
                self.call_from_thread(self._on_index_error, str(e))

    def _still_here(self) -> bool:
        """Return False once the interface is closing."""
        return not self._leaving.is_set()

    def _report_progress(self, message: str) -> None:
        """Show what the background index is doing.

        A first index runs for minutes. Without this the interface looks
        indistinguishable from one that has stopped.
        """
        if self._still_here():
            self.call_from_thread(self._show_status, message)

    def _show_status(self, message: str) -> None:
        """Write a line into the preview pane while there is nothing to preview."""
        self.query_one("#preview-pane", Static).update(f"{message}...")

    def _on_index_ready(self) -> None:
        """Called when background indexing completes successfully."""
        search_input = self.query_one(Input)
        search_input.disabled = False
        search_input.focus()

        self.query_one("#preview-pane", Static).update("Index built. Ready to search!")
        self._show_listing(self._all_chunks)

    def _on_index_error(self, error: str) -> None:
        """Called if background indexing fails."""
        self.query_one("#preview-pane", Static).update(
            f"Error building index:\n{error}"
        )

    def _show_listing(self, chunks: Sequence[Chunk], described: str = "") -> None:
        """List the chunks the filters allow, one page at a time.

        Mount at most ``limit`` rows. A large tree holds far more chunks
        than a reader can look through, and one widget for each costs
        seconds before the first keystroke: 11,543 chunks took 2.4 s.
        Say how many there are, so a short list is not read as the whole
        index.
        """
        shown = list(chunks[: self.limit])
        self._populate_results([(c, 0.0) for c in shown], show_scores=False)
        counted = (
            f"{len(shown)} of {len(chunks)}"
            if len(chunks) > len(shown)
            else str(len(chunks))
        )
        self.sub_title = "   ".join(part for part in (described, counted) if part)

    def _populate_results(
        self, results: list[tuple[Chunk, float]], *, show_scores: bool
    ) -> None:
        """Update the option list.

        Show the score prefix only for search results — the plain chunk
        listing has no meaningful scores.
        """
        self._current_results = results
        option_list = self.query_one(OptionList)
        option_list.clear_options()

        for chunk, score in results:
            locator = f"{format_selection(chunk)}  {symbol_of(chunk)}"
            label = f"[{score:.2f}] {locator}" if show_scores else locator
            option_list.add_option(Option(label))

        if results:
            option_list.highlighted = 0
            self._update_preview(0)
        else:
            self.query_one("#preview-pane", Static).update("No results found.")

    def _update_preview(self, index: int) -> None:
        """Update the preview pane for the highlighted chunk."""
        if (
            not self._current_results
            or index < 0
            or index >= len(self._current_results)
        ):
            return

        chunk, _ = self._current_results[index]
        code = load_text(chunk)
        # Highlight with the chunk's own language so every parser renders right.
        syntax = Syntax(
            code,
            chunk.language or "text",
            theme="gruvbox-dark",
            line_numbers=True,
            start_line=chunk.start_line,
            word_wrap=True,
        )
        self.query_one("#preview-pane", Static).update(syntax)

    @work(exclusive=True)
    async def do_search(self, query: str) -> None:
        """Perform semantic search with debounce."""
        # Wait out the debounce. Typing again cancels this task, since
        # the worker is exclusive.
        await asyncio.sleep(self.debounce)

        # Filters may be written into the query, as `lang:cpp under:/src/`.
        # Strip them, so the embedder sees what is wanted rather than how
        # it was narrowed.
        text, typed = parse_query(query)
        filters = typed.or_else(self._base_filters)
        self.sub_title = filters.describe()
        try:
            keep = build_result_filter(filters, self._categorize)
        except ValueError:
            # A half-typed expression is not an error to report.
            return

        self._generation += 1
        mine = self._generation

        if not text:
            # No words to search for, so list what the filters allow.
            chunks = await self._off_loop(self._listing_if_current, mine, keep)
            if chunks is None:
                return
            self._show_listing(chunks, filters.describe())
            return

        # Search off the event loop, so the interface keeps drawing.
        results = await self._off_loop(self._search_if_current, mine, text, keep)
        if results is None:
            return
        self._populate_results(results, show_scores=True)

    def _off_loop(self, function, *arguments):
        """Run *function* on the search thread, keeping the interface live."""
        return self._searcher.submit(asyncio.get_running_loop(), function, *arguments)

    def _search_if_current(self, generation: int, text: str, keep):
        """Search, unless a later keystroke has already replaced this one.

        Cancelling the task that waits on a thread does not stop the
        thread, so work queued for a query nobody wants any more would
        still be done, and the query that matters would wait behind it.
        Typing 14 characters ran 14 searches, all of them to the end.
        """
        if generation != self._generation:
            return None
        return list(self.search_use_case.search(text, self.limit, keep))

    def _listing_if_current(self, generation: int, keep):
        """List the chunks, unless a later keystroke has replaced this."""
        if generation != self._generation:
            return None
        return self.search_use_case.all_chunks(keep)

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Triggered when the user types in the search bar."""
        if not event.input.disabled:
            self.do_search(event.value)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        """Update preview pane when user navigates the results list."""
        if event.option_index is not None:
            self._update_preview(event.option_index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the selected chunk and exit."""
        self._choose(event.option_index)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Choose the highlighted result when the user presses enter."""
        self._choose(self.query_one(OptionList).highlighted)

    def action_move(self, delta: int) -> None:
        """Move the highlight without taking focus from the query field."""
        option_list = self.query_one(OptionList)
        if not option_list.option_count:
            return
        current = option_list.highlighted or 0
        target = max(0, min(option_list.option_count - 1, current + delta))
        option_list.highlighted = target
        self._update_preview(target)

    def _choose(self, index: int | None) -> None:
        """Exit with the result at *index*, if there is one."""
        if index is not None and 0 <= index < len(self._current_results):
            self.exit(self._current_results[index])
