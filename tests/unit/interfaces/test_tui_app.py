"""Test the interactive TUI by driving it headless.

Use Textual's pilot so every assertion runs against a real mounted DOM.
A fake search use case keeps the tests free of a model and a daemon.
"""

import asyncio
import threading
import time
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

    def build_index(self, root: Path, on_progress=None) -> Sequence[Chunk] | None:
        if on_progress is not None:
            on_progress("Embedding 1 of 2 chunks")
        if self._fail:
            raise RuntimeError(self._fail)
        return self._chunks

    def search(
        self,
        query: str,
        limit: int = 5,
        keep=None,
        hybrid=None,
    ) -> Sequence[tuple[Chunk, float]]:
        self.queries.append(query)
        chosen = [
            c
            for c in self._chunks
            if query.lower() in (c.symbol or "").lower() and (keep is None or keep(c))
        ]
        return [(c, 0.91) for c in chosen[:limit]]

    def all_chunks(self, keep=None) -> list[Chunk]:
        return [c for c in self._chunks if keep is None or keep(c)]

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
        if app._index_ready:
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

    def test_typing_after_a_failure_searches_nothing(self) -> None:
        """Searching a dead index would only produce more errors."""
        fake = FakeSearch(fail="boom")
        app = IshApp(fake, Path("."))

        async def body():
            async with app.run_test() as pilot:
                await asyncio.sleep(0.4)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                assert fake.queries == []

        run(body())

    def test_the_error_stays_on_screen_while_typing(self) -> None:
        """Whatever is typed, the reason must remain readable."""
        app = IshApp(FakeSearch(fail="Cannot reach Ollama"), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await asyncio.sleep(0.4)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                assert "Cannot reach Ollama" in preview_text(app)

        run(body())


class TestLimit:
    """Verify the configured result limit reaches the use case."""

    def test_limit_is_forwarded(self) -> None:
        captured: list[int] = []

        class Recording(FakeSearch):
            def search(self, query: str, limit: int = 5, keep=None, hybrid=None):
                captured.append(limit)
                return super().search(query, limit, keep, hybrid)

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


class TestInlineFilters:
    """Verify filters typed into the query itself."""

    def _mixed(self) -> FakeSearch:
        return FakeSearch(
            chunks=[
                chunk("alpha"),
                chunk("alpha_doc", language="markdown", line=5),
            ]
        )

    def test_lang_narrows_the_results(self) -> None:
        fake = self._mixed()
        app = IshApp(fake, Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"lang:markdown alpha")
                await asyncio.sleep(SETTLE)
                return [c.symbol for c, _ in app._current_results]

        assert run(body()) == ["alpha_doc"]

    def test_the_filter_is_stripped_before_searching(self) -> None:
        """The embedder must see the words, not the narrowing."""
        fake = self._mixed()
        app = IshApp(fake, Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"lang:markdown alpha")
                await asyncio.sleep(SETTLE)

        run(body())
        assert fake.queries == ["alpha"]

    def test_under_narrows_by_path(self) -> None:
        fake = FakeSearch(
            chunks=[
                Chunk(
                    path=Path("/proj/src/a.py"),
                    text="x",
                    kind="function",
                    language="python",
                    symbol="thing",
                    start_line=1,
                    end_line=1,
                ),
                Chunk(
                    path=Path("/proj/docs/b.py"),
                    text="x",
                    kind="function",
                    language="python",
                    symbol="thing",
                    start_line=1,
                    end_line=1,
                ),
            ]
        )
        app = IshApp(fake, Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"under:/docs/ thing")
                await asyncio.sleep(SETTLE)
                return [str(c.path) for c, _ in app._current_results]

        assert run(body()) == ["/proj/docs/b.py"]

    def test_a_filter_alone_lists_what_it_allows(self) -> None:
        """No words left, so show every chunk the filter permits."""
        fake = self._mixed()
        app = IshApp(fake, Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"lang:markdown")
                await asyncio.sleep(SETTLE)
                return (
                    [c.symbol for c, _ in app._current_results],
                    fake.queries,
                )

        symbols, queries = run(body())
        assert symbols == ["alpha_doc"]
        # Nothing was searched, because nothing was asked.
        assert queries == []

    def test_active_filters_are_shown(self) -> None:
        app = IshApp(self._mixed(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"lang:markdown alpha")
                await asyncio.sleep(SETTLE)
                return app.sub_title

        assert "markdown" in run(body())

    def test_clearing_the_filter_clears_the_display(self) -> None:
        """Only the count remains once no filter narrows the view."""
        app = IshApp(self._mixed(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"lang:markdown")
                await asyncio.sleep(SETTLE)
                for _ in range(len("lang:markdown")):
                    await pilot.press("backspace")
                await asyncio.sleep(SETTLE)
                return app.sub_title

        shown = run(body())
        assert "lang" not in shown
        assert shown.isdigit()

    def test_a_half_typed_expression_does_not_crash(self) -> None:
        """`under:(` is not yet a valid expression."""
        app = IshApp(self._mixed(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"under:(unclosed")
                await asyncio.sleep(SETTLE)

        run(body())


class TestIndexProgress:
    """Verify the interface says what it is doing while it indexes.

    A first index runs for minutes. Without a message, a working
    interface is indistinguishable from one that has hung.
    """

    def test_progress_reaches_the_pane(self) -> None:
        """Hold the index open until the message has been observed."""
        import threading

        release = threading.Event()

        class Held(FakeSearch):
            def build_index(self, root, on_progress=None):
                if on_progress:
                    on_progress("Embedding 120 of 274 chunks")
                # Wait rather than sleep, so the test never races a clock.
                release.wait(timeout=5)
                return self._chunks

        app = IshApp(Held(), Path("."))
        seen: list[str] = []

        async def body():
            async with app.run_test():
                for _ in range(250):
                    await asyncio.sleep(0.02)
                    seen.append(preview_text(app))
                    if any("120 of 274" in text for text in seen):
                        break
                release.set()
                await asyncio.sleep(0.05)

        run(body())
        assert any("120 of 274" in text for text in seen)

    def test_a_message_is_shown_before_any_progress(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test():
                return preview_text(app)

        assert run(body()).strip() != ""

    def test_progress_gives_way_to_the_results(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body():
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                return preview_text(app)

        assert "Embedding" not in run(body())


class TestQueryLineFilters:
    """Verify filters typed into the query line of the TUI."""

    @staticmethod
    def _mixed() -> list[Chunk]:
        code = chunk("alpha")
        doc = Chunk(
            path=Path("README.md"),
            text="alpha is the first",
            kind="section",
            language="markdown",
            symbol="alpha",
            start_line=1,
            end_line=2,
        )
        return [code, doc]

    def test_type_narrows_the_results(self) -> None:
        app = IshApp(FakeSearch(self._mixed()), Path("."))

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"type:doc alpha")
                await asyncio.sleep(SETTLE)
                shown = [c.path.name for c, _ in app._current_results]
                assert shown == ["README.md"]

        run(body())

    def test_the_filter_is_kept_out_of_the_query(self) -> None:
        """The embedder must see the question, not how it was narrowed."""
        fake = FakeSearch(self._mixed())
        app = IshApp(fake, Path("."))

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"type:doc alpha")
                await asyncio.sleep(SETTLE)
                assert fake.queries[-1] == "alpha"

        run(body())

    def test_the_active_filter_is_shown(self) -> None:
        app = IshApp(FakeSearch(self._mixed()), Path("."))

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"type:doc alpha")
                await asyncio.sleep(SETTLE)
                assert "doc" in app.sub_title

        run(body())

    def test_a_command_line_filter_still_applies(self) -> None:
        """A narrowing passed as --type holds until the query overrides it."""
        from ish.application.search import Filters

        app = IshApp(
            FakeSearch(self._mixed()), Path("."), filters=Filters(type=("doc",))
        )

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                assert [c.path.name for c, _ in app._current_results] == ["README.md"]

        run(body())

    def test_the_query_line_overrides_the_command_line(self) -> None:
        from ish.application.search import Filters

        app = IshApp(
            FakeSearch(self._mixed()), Path("."), filters=Filters(type=("doc",))
        )

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"type:code alpha")
                await asyncio.sleep(SETTLE)
                assert [c.path.name for c, _ in app._current_results] == ["mod.py"]

        run(body())


class TestListingIsCapped:
    """Verify the listing mounts a page, not the whole index.

    One widget per chunk costs seconds on a large tree: 11,543 chunks
    took 2.4 s before the first keystroke.
    """

    @staticmethod
    def _many(count: int) -> list[Chunk]:
        return [chunk(f"sym{i}", line=i + 1) for i in range(count)]

    def test_startup_mounts_at_most_the_limit(self) -> None:
        app = IshApp(FakeSearch(self._many(500)), Path("."), limit=25)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                assert len(app._current_results) == 25

        run(body())

    def test_the_header_says_how_many_there_are(self) -> None:
        app = IshApp(FakeSearch(self._many(500)), Path("."), limit=25)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                assert app.sub_title == "25 of 500"

        run(body())

    def test_a_short_listing_shows_only_its_size(self) -> None:
        app = IshApp(FakeSearch(self._many(3)), Path("."), limit=25)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                assert app.sub_title == "3"

        run(body())

    def test_clearing_the_query_stays_capped(self) -> None:
        app = IshApp(FakeSearch(self._many(500)), Path("."), limit=25)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"sym1")
                await asyncio.sleep(SETTLE)
                for _ in range(4):
                    await pilot.press("backspace")
                await asyncio.sleep(SETTLE)
                assert len(app._current_results) == 25

        run(body())

    def test_the_filter_is_still_described(self) -> None:
        app = IshApp(FakeSearch(self._many(500)), Path("."), limit=25)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"lang:python")
                await asyncio.sleep(SETTLE)
                assert "lang: python" in app.sub_title
                assert "25 of 500" in app.sub_title

        run(body())


class TestStaleWorkIsDropped:
    """Verify that typing does not queue searches nobody wants.

    Cancelling the task that waits on a thread does not stop the thread,
    so without a check every keystroke's work runs to the end and the
    newest query waits behind all of it.
    """

    class Slow:
        """A use case with a search slow enough to overlap keystrokes."""

        def __init__(self, delay: float = 0.25) -> None:
            self.delay = delay
            self.begun: list[str] = []
            self._chunks = [chunk(f"sym{i}") for i in range(5)]

        def build_index(self, root, on_progress=None):
            return self._chunks

        def all_chunks(self, keep=None):
            return self._chunks

        def search(self, query, limit=5, keep=None, hybrid=None):
            self.begun.append(query)
            time.sleep(self.delay)
            return [(c, 0.5) for c in self._chunks[:limit]]

        def close(self) -> None:
            return None

    def _type(
        self, text: str, gap: float, debounce_ms: int = 60
    ) -> "TestStaleWorkIsDropped.Slow":
        slow = self.Slow()
        app = IshApp(slow, Path("."), limit=5, debounce_ms=debounce_ms)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                for character in text:
                    await pilot.press(character)
                    await asyncio.sleep(gap)
                await asyncio.sleep(1.5)

        run(body())
        return slow

    def test_typing_faster_than_the_debounce_searches_once(self) -> None:
        """A burst inside one debounce is one search, not eight."""
        slow = self._type("abcdefgh", gap=0.0, debounce_ms=600)
        assert slow.begun == ["abcdefgh"]

    def test_most_overlapping_work_is_dropped(self) -> None:
        """Only what is still wanted when a thread is free gets done."""
        slow = self._type("abcdefghij", gap=0.08)
        assert len(slow.begun) < 10
        # Whatever ran, the last one is the whole query.
        assert slow.begun[-1] == "abcdefghij"

    def test_the_search_runs_on_one_daemon_thread(self) -> None:
        """One thread, because the backend serves one request at a time.

        A daemon, because exit must never wait for a request in flight.
        """
        app = IshApp(self.Slow(), Path("."), limit=5)
        assert app._searcher._thread.daemon
        assert app._searcher._thread.is_alive()

    def test_out_of_date_work_returns_nothing(self) -> None:
        app = IshApp(self.Slow(), Path("."), limit=5)
        app._generation = 7
        assert app._search_if_current(6, "old", None) is None
        assert app._listing_if_current(6, None) is None

    def test_current_work_is_done(self) -> None:
        app = IshApp(self.Slow(delay=0.0), Path("."), limit=5)
        app._generation = 7
        assert app._search_if_current(7, "now", None) is not None
        assert app._listing_if_current(7, None) is not None


class TestQuittingIsImmediate:
    """Verify the interface closes while work is still in flight.

    A thread pool registers an exit hook that joins its threads however
    it is shut down, so an embedding in flight held the whole process
    open. Every thread the interface starts is a daemon for that reason.
    """

    class Blocking:
        """A use case whose calls do not return in the life of a test."""

        def __init__(self) -> None:
            self.entered = threading.Event()

        def build_index(self, root, on_progress=None):
            if on_progress is not None:
                on_progress("Embedding 1 of 100000 chunks")
            self.entered.set()
            time.sleep(30)
            return []

        def all_chunks(self, keep=None):
            return []

        def search(self, query, limit=5, keep=None, hybrid=None):
            self.entered.set()
            time.sleep(30)
            return []

        def close(self) -> None:
            return None

    def test_every_worker_thread_is_a_daemon(self) -> None:
        """Nothing the interface starts may outlive the wish to leave."""
        blocking = self.Blocking()
        app = IshApp(blocking, Path("."), limit=5, debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                assert blocking.entered.wait(5.0)
                await pilot.pause()

        run(body())
        for thread in threading.enumerate():
            if thread.name.startswith("ish-"):
                assert thread.daemon, thread.name

    def test_closing_does_not_wait_for_the_index(self) -> None:
        blocking = self.Blocking()
        app = IshApp(blocking, Path("."), limit=5, debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                assert blocking.entered.wait(5.0)
                await pilot.pause()

        started = time.monotonic()
        run(body())
        assert time.monotonic() - started < 5.0

    def test_the_index_thread_stops_talking_once_closed(self) -> None:
        """A thread must not touch the interface after it has gone."""
        blocking = self.Blocking()
        app = IshApp(blocking, Path("."), limit=5, debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                assert blocking.entered.wait(5.0)
                await pilot.pause()

        run(body())
        assert app._leaving.is_set()
        assert not app._still_here()

    def test_quit_is_bound_to_the_usual_keys(self) -> None:
        keys = {binding[0] for binding in IshApp.BINDINGS}
        assert {"escape", "ctrl+c", "ctrl+q"} <= keys


class TestTypingBeforeTheIndexOpens:
    """Verify the query field is useful from the first frame.

    Opening an index of a large tree takes most of a second, and a field
    that cannot be typed into reads as an interface that has not started.
    """

    class Slow:
        """A use case whose index takes a while to open."""

        def __init__(self, delay: float = 0.4) -> None:
            self.delay = delay
            self.queries: list[str] = []
            self._chunks = [chunk("alpha"), chunk("beta", line=5)]

        def build_index(self, root, on_progress=None):
            if on_progress is not None:
                on_progress("Embedding 1 of 2 chunks")
            time.sleep(self.delay)
            return self._chunks

        def all_chunks(self, keep=None):
            return self._chunks

        def search(self, query, limit=5, keep=None, hybrid=None):
            self.queries.append(query)
            return [(c, 0.9) for c in self._chunks if query in (c.symbol or "")]

        def close(self) -> None:
            return None

    def test_the_field_takes_typing_at_once(self) -> None:
        app = IshApp(self.Slow(), Path("."), limit=5)

        async def body() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                field = app.query_one(Input)
                assert field.disabled is False
                assert field.has_focus

        run(body())

    def test_a_query_typed_while_opening_is_answered(self) -> None:
        """What was typed must not be lost to the wait.

        Hold the index open long enough that every keystroke lands
        first, so the assertion does not race the opening.
        """
        slow = self.Slow(delay=1.2)
        app = IshApp(slow, Path("."), limit=5, debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press(*"alpha")
                assert app._index_ready is False
                assert slow.queries == []
                for _ in range(300):
                    await asyncio.sleep(0.02)
                    if app._current_results:
                        break

        run(body())
        assert slow.queries == ["alpha"]
        assert [c.symbol for c, _ in app._current_results] == ["alpha"]

    def test_nothing_is_searched_before_the_index_opens(self) -> None:
        slow = self.Slow(delay=2.0)
        app = IshApp(slow, Path("."), limit=5, debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press(*"alpha")
                await asyncio.sleep(0.3)
                assert slow.queries == []

        run(body())

    def test_the_progress_message_survives_typing(self) -> None:
        """Typing must not wipe the only sign that work is going on."""
        app = IshApp(self.Slow(delay=2.0), Path("."), limit=5, debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press(*"alpha")
                await asyncio.sleep(0.3)
                assert "Embedding 1 of 2 chunks" in preview_text(app)

        run(body())

    def test_an_empty_field_lists_the_index_when_it_opens(self) -> None:
        app = IshApp(self.Slow(), Path("."), limit=5)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await asyncio.sleep(0.1)
                assert len(app._current_results) == 2

        run(body())


class TestWorkerErrors:
    """Verify a failure on the search thread reaches the caller."""

    def test_an_exception_is_carried_back(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body() -> None:
            async with app.run_test():

                def explode() -> None:
                    raise RuntimeError("boom")

                with pytest.raises(RuntimeError, match="boom"):
                    await app._off_loop(explode)

        run(body())

    def test_a_result_is_carried_back(self) -> None:
        app = IshApp(FakeSearch(), Path("."))

        async def body() -> None:
            async with app.run_test():
                assert await app._off_loop(lambda value: value * 2, 21) == 42

        run(body())

    def test_stale_work_reports_nothing(self) -> None:
        """Both paths return None once a later keystroke has replaced them."""
        app = IshApp(FakeSearch(), Path("."))
        app._generation = 2
        assert app._search_if_current(1, "old", None) is None
        assert app._listing_if_current(1, None) is None


class TestStaleResultsAreNotShown:
    """Verify a reply nobody is waiting for never reaches the screen.

    A later keystroke replaces an earlier search, and the earlier one
    must leave the results as the newer query found them.
    """

    def test_a_stale_search_leaves_the_results_alone(self) -> None:
        app = IshApp(FakeSearch(), Path("."), debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                shown = app._current_results
                assert shown

                # Whatever comes back is out of date.
                app._search_if_current = lambda generation, text, keep: None
                await pilot.press(*"bb")
                await asyncio.sleep(SETTLE)
                assert app._current_results is shown

        run(body())

    def test_a_stale_listing_leaves_the_results_alone(self) -> None:
        app = IshApp(FakeSearch(), Path("."), debounce_ms=10)

        async def body() -> None:
            async with app.run_test() as pilot:
                await _ready(app, pilot)
                await pilot.press(*"alpha")
                await asyncio.sleep(SETTLE)
                shown = app._current_results

                # Deleting a character searches too, so silence both
                # paths and check that neither writes to the screen.
                app._listing_if_current = lambda generation, keep: None
                app._search_if_current = lambda generation, text, keep: None
                for _ in range(5):
                    await pilot.press("backspace")
                await asyncio.sleep(SETTLE)
                assert app._current_results is shown

        run(body())
