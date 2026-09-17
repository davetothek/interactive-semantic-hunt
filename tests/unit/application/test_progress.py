"""Test how an index step renders."""

from pathlib import Path

from ish.application.progress import (
    DISCOVER,
    EMBED,
    READ,
    READY,
    REFRESH,
    Progress,
)


class TestRendering:
    """Verify each stage reads as the sentence an interface shows."""

    def test_discover(self) -> None:
        assert str(Progress(DISCOVER)) == "Looking for source files"

    def test_read(self) -> None:
        assert str(Progress(READ, done=3, total=9)) == "Reading 3 of 9 files"

    def test_embedding_begins_with_what_is_reused(self) -> None:
        assert (
            str(Progress(EMBED, total=40, reused=7)) == "Embedding 40 chunks (7 reused)"
        )

    def test_embedding_counts_up(self) -> None:
        assert str(Progress(EMBED, done=25, total=40)) == "Embedded 25 of 40 chunks"

    def test_ready(self) -> None:
        assert str(Progress(READY, total=12)) == "Index ready: 12 files"


class TestWithinATree:
    """Verify a step names the tree it belongs to when several are refreshed."""

    def test_the_refresh_step_alone_names_the_tree(self) -> None:
        step = Progress(REFRESH).within(Path("/p/src"), 1, 2)
        assert str(step) == "Refreshing 1 of 2: src"

    def test_a_step_inside_keeps_the_tree_beside_it(self) -> None:
        step = Progress(READ, 3, 9).within(Path("/p/src"), 1, 2)
        assert str(step) == "Refreshing 1 of 2: src, Reading 3 of 9 files"
        assert step.tree == Path("/p/src")

    def test_a_step_outside_any_tree_stands_alone(self) -> None:
        assert str(Progress(READ, 3, 9)) == "Reading 3 of 9 files"
