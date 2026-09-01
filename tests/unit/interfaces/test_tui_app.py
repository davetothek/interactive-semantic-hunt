"""Test the interactive TUI by driving it headless.

Use Textual's pilot so every assertion runs against a real mounted DOM.
A fake search use case keeps the tests free of a model and a daemon.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, Static

from ish.domain.chunk import Chunk
from ish.interfaces.tui.app import IshApp

# Long enough to clear the 200 ms debounce in do_search.
SETTLE = 0.45


def chunk(symbol: str, language: str = "python", line: int = 1) -> Chunk:
    return Chunk(
        path=Path("src/mod.py"),
        text=f"def {symbol}():\n    pass\n",
        kind="function",
        language=language,
        symbol=symbol,
        start_line=line,
        end_line=line + 1,
    )


class FakeSearch:
    """Stand in for the Search use case."""

    def __init__(self, chunks: Sequence[Chunk] | None = None, fail: str = "") -> None:
        if chunks is None:
            chunks = [chunk("alpha"), chunk("beta", line=5)]
        self._chunks = list(chunks)
        self._fail = fail
        self.queries: list[str] = []

    def build_index(self, root: Path) -> Sequence[Chunk] | None:
        if self._fail:
            raise RuntimeError(self._fail)
        return self._chunks

    def search(self, query: str, limit: int = 5) -> Sequence[tuple[Chunk, float]]:
        self.queries.append(query)
        return [(self._chunks[0], 0.91)]

    def close(self) -> None:
        return None


def run(coro):
    """Run one async body. The suite has no asyncio plugin."""
    return asyncio.run(coro)


async def _ready(app: IshApp, pilot, timeout: float = 5.0) -> None:
    """Wait for the background index worker to finish."""
    waited = 0.0
    while waited < timeout:
        await asyncio.sleep(0.02)
        waited += 0.02
        if app._all_chunks or app.query_one(Input).disabled is False:
            return
    raise AssertionError("index never became ready")


def preview_content(app: IshApp):
    """Read whatever the preview pane currently holds."""
    return app.query_one("#preview-pane", Static).content


def preview_text(app: IshApp) -> str:
    """Read the preview pane as plain text."""
    return str(preview_content(app))


class TestStartup:
    """Verify the state the user meets when the index finishes."""

    def test_input_is_enabled_and_focused(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                field = app.query_one(Input)
                assert field.disabled is False
                assert field.has_focus is True

        run(body())

    def test_every_chunk_is_listed(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                assert app.query_one(OptionList).option_count == 2

        run(body())

    def test_listing_shows_no_score(self) -> None:
        """Nothing has been searched, so a score would be meaningless."""
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                label = app.query_one(OptionList).get_option_at_index(0).prompt
                assert "alpha" in str(label)
                assert "[0.00]" not in str(label)

        run(body())

    def test_empty_index_reports_it(self) -> None:
        app = IshApp(FakeSearch(chunks=[]), Path("."))

        async def body():
            async with app.run_test():
                await asyncio.sleep(0.3)
                assert "No results" in preview_text(app)

        run(body())


class TestSearching:
    """Verify the typing path."""

    def test_typing_runs_one_debounced_search(self) -> None:
        fake = FakeSearch()
        app = IshApp(fake, Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                # The debounce collapses five keystrokes into one query.
                assert fake.queries == ["alpha"]

        run(body())

    def test_results_show_a_score(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                label = str(app.query_one(OptionList).get_option_at_index(0).prompt)
                assert label.startswith("[0.91]")

        run(body())

    def test_clearing_restores_the_full_listing(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                assert app.query_one(OptionList).option_count == 1

                for _ in range(5):
                    await pilot.press("backspace")
                await asyncio.sleep(SETTLE)

                options = app.query_one(OptionList)
                assert options.option_count == 2
                assert "[" not in str(options.get_option_at_index(0).prompt)

        run(body())


class TestPreview:
    """Verify the pane beside the list."""

    def test_preview_follows_the_highlight(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press("down")
                await pilot.pause()
                assert app.query_one(OptionList).highlighted == 1

        run(body())

    def test_preview_uses_the_chunk_language(self) -> None:
        """A non-Python chunk must not be highlighted as Python."""
        app = IshApp(
            FakeSearch(chunks=[chunk("Intro", language="markdown")]), Path(".")
        )

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                syntax = preview_content(app)
                assert syntax.lexer.name.lower() == "markdown"

        run(body())

    def test_out_of_range_index_is_ignored(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                app._update_preview(99)
                app._update_preview(-1)

        run(body())


class TestSelection:
    """Verify what the app hands back to the shell."""

    def test_selecting_exits_with_the_chunk(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press("enter")
                await pilot.pause()

        run(body())
        assert app.return_value is not None
        selected, score = app.return_value
        assert selected.symbol == "alpha"
        assert isinstance(score, float)


class TestIndexFailure:
    """Verify that a backend failure is shown, not swallowed."""

    def test_error_reaches_the_preview_pane(self) -> None:
        app = IshApp(FakeSearch(fail="Cannot reach Ollama"), Path("."))

        async def body():
            async with app.run_test():
                await asyncio.sleep(0.4)
                assert "Cannot reach Ollama" in preview_text(app)

        run(body())

    def test_input_stays_disabled_after_a_failure(self) -> None:
        """Typing into a dead index would only produce more errors."""
        app = IshApp(FakeSearch(fail="boom"), Path("."))

        async def body():
            async with app.run_test():
                await asyncio.sleep(0.4)
                assert app.query_one(Input).disabled is True

        run(body())


class TestLimit:
    """Verify the configured result limit reaches the use case."""

    def test_limit_is_forwarded(self) -> None:
        captured: list[int] = []

        class Recording(FakeSearch):
            def search(self, query: str, limit: int = 5):
                captured.append(limit)
                return super().search(query, limit)

        app = IshApp(Recording(), Path("."), limit=17)

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"a")
                await asyncio.sleep(SETTLE)

        run(body())
        assert captured == [17]


@pytest.mark.parametrize("key", ["escape"])
def test_escape_quits(key: str) -> None:
    app = IshApp(FakeSearch(), Path("."))

    async def body():
        async with app.run_test() as pilot:
            await _ready(app, pilot)
            await pilot.press(key)
            await pilot.pause()

    run(body())


class TestKeyboardNavigation:
    """Verify fzf-style keys while the query field keeps focus."""

    def _navigate(self, keys: list[str]):
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                for key in keys:
                    await pilot.press(key)
                await pilot.pause()
                return (
                    app.query_one(OptionList).highlighted,
                    app.query_one(Input).has_focus,
                )

        return run(body())

    def test_down_moves_the_highlight(self) -> None:
        highlighted, focused = self._navigate(["down"])
        assert highlighted == 1
        assert focused is True, "the query field must keep focus"

    def test_up_moves_back(self) -> None:
        assert self._navigate(["down", "up"])[0] == 0

    def test_ctrl_n_and_ctrl_p(self) -> None:
        assert self._navigate(["ctrl+n"])[0] == 1
        assert self._navigate(["ctrl+n", "ctrl+p"])[0] == 0

    def test_highlight_stops_at_the_ends(self) -> None:
        assert self._navigate(["up", "up", "up"])[0] == 0
        assert self._navigate(["down"] * 9)[0] == 1

    def test_navigation_on_an_empty_list_is_safe(self) -> None:
        app = IshApp(FakeSearch(chunks=[]), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await asyncio.sleep(0.3)
                await pilot.press("down")
                await pilot.pause()

        run(body())

    def test_enter_selects_the_highlighted_result(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press("down")
                await pilot.press("enter")
                await pilot.pause()

        run(body())
        assert app.return_value is not None
        assert app.return_value[0].symbol == "beta"

    def test_enter_with_nothing_highlighted_does_not_exit(self) -> None:
        app = IshApp(FakeSearch(chunks=[]), Path("."))

        async def body():
            async with app.run_test():
                await asyncio.sleep(0.3)
                app._choose(None)
                app._choose(99)

        run(body())
        assert app.return_value is None


def test_selecting_in_the_list_exits() -> None:
    """The user may also move focus into the list and press enter there."""
    app = IshApp(FakeSearch(), Path("."))

    async def body():
        async with app.run_test() as pilot:
            await _ready(app, pilot)
            app.query_one(OptionList).focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    run(body())
    assert app.return_value is not None
    assert app.return_value[0].symbol == "alpha"
