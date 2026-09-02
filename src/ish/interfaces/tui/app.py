"""Interactive Textual UI for Semantic Search."""

import asyncio
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
        filters: Filters | None = None,
    ) -> None:
        super().__init__()
        self.search_use_case = search_use_case
        self.root_path = root_path
        self.limit = limit
        # What the command line already narrowed. A filter typed into
        # the query line overrides it for as long as it is typed.
        self._base_filters = filters or Filters()
        self._current_results: list[tuple[Chunk, float]] = []
        self._all_chunks: list[Chunk] = []

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

    @work(thread=True)
    def build_index(self) -> None:
        """Scan and embed the directory in a background thread."""
        try:
            chunks = self.search_use_case.build_index(
                self.root_path, self._report_progress
            )
            self._all_chunks = list(chunks) if chunks else []
            self.call_from_thread(self._on_index_ready)
        except Exception as e:
            self.call_from_thread(self._on_index_error, str(e))

    def _report_progress(self, message: str) -> None:
        """Show what the background index is doing.

        A first index runs for minutes. Without this the interface looks
        indistinguishable from one that has stopped.
        """
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
        self._populate_results([(c, 0.0) for c in self._all_chunks], show_scores=False)

    def _on_index_error(self, error: str) -> None:
        """Called if background indexing fails."""
        self.query_one("#preview-pane", Static).update(
            f"Error building index:\n{error}"
        )

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
        # 200ms debounce: if the user types again,
        # this task is cancelled by exclusive=True
        await asyncio.sleep(0.2)

        # Filters may be written into the query, as `lang:cpp under:/src/`.
        # Strip them, so the embedder sees what is wanted rather than how
        # it was narrowed.
        text, typed = parse_query(query)
        filters = typed.or_else(self._base_filters)
        self.sub_title = filters.describe()
        try:
            keep = build_result_filter(filters)
        except ValueError:
            # A half-typed expression is not an error to report.
            return

        if not text:
            # No words left, so show every chunk the filters allow.
            chunks = await asyncio.to_thread(self.search_use_case.all_chunks, keep)
            self._populate_results([(c, 0.0) for c in chunks], show_scores=False)
            return

        # Perform the actual ML search in a background thread so UI doesn't freeze
        results = await asyncio.to_thread(
            self.search_use_case.search, text, self.limit, keep
        )
        self._populate_results(list(results), show_scores=True)

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
