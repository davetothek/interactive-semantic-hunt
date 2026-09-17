"""Say what a long index run is doing, as a value rather than a sentence.

A first index of a large tree runs for minutes, and an interface that
shows nothing cannot be told from one that has hung. Each interface
renders progress its own way: the CLI rewrites one line, the TUI fills a
pane, the MCP server answers a status call. They need the numbers, not
a message to parse, so the event carries the stage and the counts and
renders itself only when asked.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

DISCOVER = "discover"
"""Walking the tree for source files."""
READ = "read"
"""Reading and parsing the files that changed."""
EMBED = "embed"
"""Embedding the chunks the store has never seen."""
READY = "ready"
"""The index is in step with the tree."""
REFRESH = "refresh"
"""Starting on one tree of several."""


@dataclass(frozen=True, slots=True)
class Progress:
    """Report one step of an index run.

    *done* and *total* count the unit the stage works in: files while
    reading, chunks while embedding. *tree* names the tree being
    refreshed when several are, with its position among them.
    """

    stage: str
    done: int = 0
    total: int = 0
    reused: int = 0
    tree: Path | None = None
    tree_number: int = 0
    tree_count: int = 0

    def within(self, tree: Path, number: int, count: int) -> "Progress":
        """Return this step, placed inside a refresh of several trees."""
        return replace(self, tree=tree, tree_number=number, tree_count=count)

    def __str__(self) -> str:
        """Render the step as one line a person can read."""
        parts = []
        if self.tree is not None:
            parts.append(
                f"Refreshing {self.tree_number} of {self.tree_count}: {self.tree.name}"
            )
        if self.stage == DISCOVER:
            parts.append("Looking for source files")
        elif self.stage == READ:
            parts.append(f"Reading {self.done} of {self.total} files")
        elif self.stage == EMBED and self.done == 0:
            parts.append(f"Embedding {self.total} chunks ({self.reused} reused)")
        elif self.stage == EMBED:
            parts.append(f"Embedded {self.done} of {self.total} chunks")
        elif self.stage == READY:
            parts.append(f"Index ready: {self.total} files")
        return ", ".join(parts)


ProgressCallback = Callable[[Progress], None]
"""Receive each step as it happens."""
