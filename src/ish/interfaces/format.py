"""Shared presentation helpers for the CLI and TUI."""

from pathlib import Path

from ish.domain.chunk import Chunk


def display_path(path: Path) -> Path:
    """Return *path* relative to the working directory when possible."""
    try:
        return path.relative_to(Path.cwd())
    except ValueError:
        return path


def format_chunk_line(chunk: Chunk) -> str:
    """Format one chunk in the plain scan output format."""
    return (
        f"{display_path(chunk.path)}:{chunk.start_line}-{chunk.end_line}  "
        f"{chunk.kind}  {symbol_of(chunk)}"
    )


def symbol_of(chunk: Chunk) -> str:
    """Return the chunk's name, or a marker when it has none."""
    return chunk.symbol or "<anonymous>"


def format_result_line(chunk: Chunk, score: float) -> str:
    """Format one ranked search result."""
    return f"[{score:.2f}] {format_chunk_line(chunk)}"


def format_grep_line(chunk: Chunk, score: float | None = None) -> str:
    """Format a result the way a grep-driven editor expects.

    ``path:line:column:text`` is what an editor picker parses to open a
    file at a position and to preview it, so emitting it directly saves
    every caller from parsing the human format.
    """
    prefix = f"[{score:.2f}] " if score is not None else ""
    return (
        f"{display_path(chunk.path)}:{chunk.start_line}:1:"
        f"{prefix}{chunk.kind}  {symbol_of(chunk)}"
    )


def format_selection(chunk: Chunk) -> str:
    """Format a selected chunk as an editor-friendly ``path:line`` locator."""
    return f"{display_path(chunk.path)}:{chunk.start_line}"
