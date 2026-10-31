"""Shared presentation helpers for the CLI and TUI."""

from collections.abc import Callable, Sequence
from pathlib import Path

from ish.application.answer import Answer, AnswerBlock, AnswerPart
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


def number_citations(
    answer: Answer, numbers: dict[Chunk, int] | None = None
) -> tuple[str, list[Chunk]]:
    """Return the answer text with numbered citations, and the cited chunks.

    Number each cited chunk once, in the order the answer first cites
    it, and mark each part of the text with the numbers it cites. Chunk
    *n* of the list is citation ``[n]``. Continue *numbers* when given,
    so a layout of several blocks numbers its citations once, in order.
    """
    numbers = {} if numbers is None else numbers
    text = ""
    for part in answer.parts:
        marks = "".join(
            f"[{numbers.setdefault(c, len(numbers) + 1)}]" for c in part.cites
        )
        # Keep the markers on the claim they cite: a part that ends a
        # paragraph ends in newlines, and the markers go before them.
        body = part.text.rstrip()
        piece = f"{body}{marks}{part.text[len(body) :]}"
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


# Columns an answer wraps to. A heading stays on one line, and a link is
# never broken, so those two may run past it.
WIDTH = 80


def format_answer(
    answer: Answer,
    question: str,
    link: Callable[[Chunk], str],
    base: Path | None = None,
    kind_of: Callable[[Chunk], str] | None = None,
) -> str:
    """Format an answer as Markdown that a person reads from the top down.

    Open with the short answer. Give each part of the question its own
    section, a procedure as numbered steps, and registers or values as a
    table. Gather what the sources leave open in one block, so a reader
    sees what is not settled. Wrap text to ``WIDTH`` columns; a table, a
    link, and a directory stay whole. List each source as a link with its
    file name and lines, and below it what kind of file it is, its
    heading, and its directory. For a question the documents do not
    cover, list the closest sources.
    """
    lines = [f"# {_one_line(question)}", ""]
    numbers: dict[Chunk, int] = {}
    if answer.covered and answer.blocks:
        for block in answer.blocks:
            lines += _render(block, numbers) + [""]
        cited = list(numbers)
        lines += ["## Sources", ""]
    elif answer.covered:
        text, cited = number_citations(answer)
        for paragraph in text.split("\n\n"):
            if paragraph.strip():
                lines += [_fill(paragraph), ""]
        lines += ["## Sources", ""]
    else:
        cited = list(answer.sources)
        lines += [_fill(NOT_COVERED_TEXT, "> "), "", "## Closest sources", ""]
    for n, chunk in enumerate(cited, 1):
        marker = f"{n}. "
        place = f"{chunk.path.name}{chunk.location.render()}"
        lines.append(f"{marker}[{place}]({link(chunk)})")
        indent = " " * len(marker)
        about = [kind_of(chunk)] if kind_of else []
        lines.append(_fill(" · ".join([*about, symbol_of(chunk)]), indent))
        # A directory does not wrap: a path broken over two lines no longer
        # pastes into a shell.
        lines.append(f"{indent}{display_path(chunk.path.parent, base).as_posix()}/")
    if answer.searches:
        lines += ["", "## Follow-up searches", ""]
        lines += [_fill(query, "- ") for query in answer.searches]
    return "\n".join(lines)


def layout_data(answer: Answer) -> tuple[list[dict[str, object]], list[Chunk]]:
    """Return the layout of an answer as plain data, and the cited chunks.

    Number the citations in reading order, as ``format_answer`` prints
    them, so a client that shows the data and one that shows the text
    agree on what ``[n]`` means. An answer with no layout is one prose
    block.
    """
    numbers: dict[Chunk, int] = {}
    blocks = answer.blocks or (AnswerBlock(kind="prose", parts=answer.parts),)
    data: list[dict[str, object]] = []
    for block in blocks:
        entry: dict[str, object] = {"kind": block.kind, "heading": block.heading}
        if block.kind in ("steps", "gaps"):
            entry["items"] = [
                f"{p.text.strip()}{_marks(p.cites, numbers)}" for p in block.parts
            ]
        else:
            entry["text"] = _joined(block.parts, numbers)
        if block.kind == "table":
            entry["columns"] = list(block.columns)
            entry["rows"] = [
                {"cells": list(r.cells), "cites": _marks(r.cites, numbers)}
                for r in block.rows
            ]
        data.append(entry)
    return data, list(numbers)


def _marks(cites: Sequence[Chunk], numbers: dict[Chunk, int]) -> str:
    return "".join(f"[{numbers.setdefault(c, len(numbers) + 1)}]" for c in cites)


def _joined(parts: Sequence[AnswerPart], numbers: dict[Chunk, int]) -> str:
    """Join parts into text with their markers, as ``number_citations`` does."""
    return number_citations(
        Answer(parts=tuple(parts), covered=True, sources=()), numbers
    )[0]


def _render(block: AnswerBlock, numbers: dict[Chunk, int]) -> list[str]:
    """Render one block as Markdown lines, numbering new citations in order."""
    lines = [f"### {block.heading}", ""] if block.heading else []
    if block.kind == "summary":
        return [_fill(f"**Short answer:** {_joined(block.parts, numbers)}")]
    if block.kind == "gaps":
        lines = ["> **Not in the documents**", ">"]
        for part in block.parts:
            text = f"{part.text.strip()}{_marks(part.cites, numbers)}"
            lines += _fill(text, "> - ").replace("\n> - ", "\n>   ").split("\n")
        return lines
    if block.kind == "steps":
        for n, part in enumerate(block.parts, 1):
            marker = f"{n}. "
            text = f"{marker}{part.text.strip()}{_marks(part.cites, numbers)}"
            lines.append(_fill(text, "", " " * len(marker)))
        return lines
    if block.kind == "table":
        if block.parts:
            lines += [_fill(_joined(block.parts, numbers)), ""]
        return lines + _table(block, numbers)
    for paragraph in _joined(block.parts, numbers).split("\n\n"):
        if paragraph.strip():
            lines += [_fill(paragraph), ""]
    return lines[:-1] if lines and lines[-1] == "" else lines


def _table(block: AnswerBlock, numbers: dict[Chunk, int]) -> list[str]:
    """Render a table with a column of citations, padded to line up in a terminal."""
    width = max([len(block.columns)] + [len(r.cells) for r in block.rows])
    head = [*block.columns, *[""] * (width - len(block.columns)), "Sources"]
    body = [
        [*r.cells, *[""] * (width - len(r.cells)), _marks(r.cites, numbers)]
        for r in block.rows
    ]
    cells = [[_cell(c) for c in row] for row in [head, *body]]
    sizes = [max(len(row[i]) for row in cells) for i in range(width + 1)]
    lines = []
    for n, row in enumerate(cells):
        lines.append(
            "| "
            + " | ".join(c.ljust(s) for c, s in zip(row, sizes, strict=True))
            + " |"
        )
        if n == 0:
            lines.append("|" + "|".join("-" * (s + 2) for s in sizes) + "|")
    return lines


def _cell(text: str) -> str:
    return _one_line(text).replace("|", "\\|")


def _fill(text: str, indent: str = "", hanging: str | None = None) -> str:
    """Wrap *text* to ``WIDTH``, never inside a word.

    Start the first line with *indent*, and every later line with
    *hanging*, which is *indent* unless given.
    """
    import textwrap

    return textwrap.fill(
        _one_line(text),
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent if hanging is None else hanging,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _one_line(text: str) -> str:
    return " ".join(text.split())
