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
        f"{chunk.kind}  {chunk.symbol}"
    )


def format_result_line(chunk: Chunk, score: float) -> str:
    """Format one ranked search result."""
    return f"[{score:.2f}] {format_chunk_line(chunk)}"


def format_selection(chunk: Chunk) -> str:
    """Format a selected chunk as an editor-friendly ``path:line`` locator."""
    return f"{display_path(chunk.path)}:{chunk.start_line}"
