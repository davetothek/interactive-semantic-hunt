"""Shared presentation helpers for the CLI and TUI."""

from pathlib import Path

from ish.application.answer import Answer
from ish.domain.chunk import Chunk


def display_path(path: Path, base: Path | None = None) -> Path:
    """Return *path* relative to *base*, or to the working directory, when possible.

    A server that answers for several callers names the directory each
    one asked about, because its own working directory means nothing to
    them. A caller that resolves the answer against that directory, as
    an editor does, then opens the right file.
    """
    try:
        return path.relative_to(base if base is not None else Path.cwd())
    except ValueError:
        return path


def format_chunk_line(chunk: Chunk, base: Path | None = None) -> str:
    """Format one chunk in the plain scan output format."""
    return (
        f"{display_path(chunk.path, base)}{chunk.location.render()}  "
        f"{chunk.kind}  {symbol_of(chunk)}"
    )


def symbol_of(chunk: Chunk) -> str:
    """Return the chunk's name, or a marker when it has none."""
    return chunk.symbol or "<anonymous>"


def format_result_line(chunk: Chunk, score: float, base: Path | None = None) -> str:
    """Format one ranked search result."""
    return f"[{score:.2f}] {format_chunk_line(chunk, base)}"


def format_grep_line(
    chunk: Chunk, score: float | None = None, base: Path | None = None
) -> str:
    """Format a result the way a grep-driven editor expects.

    ``path:line:column:text`` is what an editor picker parses to open a
    file at a position and to preview it, so emitting it directly saves
    every caller from parsing the human format.
    """
    prefix = f"[{score:.2f}] " if score is not None else ""
    return (
        f"{display_path(chunk.path, base)}:{chunk.start_line}:1:"
        f"{prefix}{chunk.kind}  {symbol_of(chunk)}"
    )


def render(
    chunk: Chunk,
    shape: str,
    score: float | None = None,
    *,
    base: Path | None = None,
) -> str:
    """Render one chunk in the shape the caller asked for.

    Accept a score for a ranked result and none for a plain listing, so
    every interface formats a line through this one function. Write the
    path from *base* when one is given, and from the working directory
    otherwise.
    """
    if shape == "grep":
        return format_grep_line(chunk, score, base)
    if score is None:
        return format_chunk_line(chunk, base)
    return format_result_line(chunk, score, base)


def format_selection(chunk: Chunk) -> str:
    """Format a selected chunk as an editor-friendly ``path:line`` locator."""
    return f"{display_path(chunk.path)}:{chunk.start_line}"


NOT_COVERED_TEXT = "The documents do not answer this. The closest sources:"


def number_citations(answer: Answer) -> tuple[str, list[Chunk]]:
    """Return the answer text with numbered citations, and the cited chunks.

    Number each cited chunk once, in the order the answer first cites
    it, and mark each part of the text with the numbers it cites. Chunk
    *n* of the list is citation ``[n]``.
    """
    numbers: dict[Chunk, int] = {}
    text = ""
    for part in answer.parts:
        marks = "".join(
            f"[{numbers.setdefault(c, len(numbers) + 1)}]" for c in part.cites
        )
        piece = f"{part.text}{marks}"
        # A model may split sentences into parts with no space between
        # them. Add one, but never before punctuation that ends a clause.
        if (
            text
            and piece
            and not text[-1].isspace()
            and not piece[0].isspace()
            and piece[0] not in ".,;:!?)"
        ):
            text += " "
        text += piece
    return text.strip(), list(numbers)


def format_answer(answer: Answer, base: Path | None = None) -> str:
    """Format an answer as text with numbered citations, then its sources.

    List the sources below in the scan format, so each one opens in an
    editor. For a question the documents do not cover, list the closest
    sources.
    """
    if not answer.covered:
        lines = [NOT_COVERED_TEXT, ""]
        lines += [format_chunk_line(c, base) for c in answer.sources]
        return "\n".join(lines)
    text, cited = number_citations(answer)
    lines = [text, ""]
    lines += [f"[{n}] {format_chunk_line(c, base)}" for n, c in enumerate(cited, 1)]
    return "\n".join(lines)
