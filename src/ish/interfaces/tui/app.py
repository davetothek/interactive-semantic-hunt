"""Interactive Textual UI for Semantic Search."""

import asyncio
from pathlib import Path

from rich.syntax import Syntax
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from ish.application.search import Search
from ish.domain.chunk import Chunk
from ish.interfaces.format import format_selection


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
        self, search_use_case: Search, root_path: Path, *, limit: int = 50
    ) -> None:
        super().__init__()
        self.search_use_case = search_use_case
        self.root_path = root_path
        self.limit = limit
        self._current_results: list[tuple[Chunk, float]] = []
        self._all_chunks: list[Chunk] = []

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with Horizontal(id="main-container"):
            yield OptionList(id="results-list")
            yield Static("Loading index...", id="preview-pane")
        yield Input(
            placeholder="Semantic search query...", id="search-input", disabled=True
        )
        yield Footer()

    def on_mount(self) -> None:
        """Start the background indexing task when UI mounts."""
        self.build_index()

    @work(thread=True)
    def build_index(self) -> None:
        """Scan and embed the directory in a background thread."""
        try:
            chunks = self.search_use_case.build_index(self.root_path)
            self._all_chunks = list(chunks) if chunks else []
            self.call_from_thread(self._on_index_ready)
        except Exception as e:
            self.call_from_thread(self._on_index_error, str(e))

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
            locator = f"{format_selection(chunk)}  {chunk.symbol}"
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
        code = chunk.text
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

        if not query:
            # Revert to showing all chunks if search is cleared
            self._populate_results(
                [(c, 0.0) for c in self._all_chunks], show_scores=False
            )
            return

        # Perform the actual ML search in a background thread so UI doesn't freeze
        results = await asyncio.to_thread(
            self.search_use_case.search, query, limit=self.limit
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
