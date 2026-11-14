"""Shared presentation helpers for the CLI and TUI."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ish.application.answer import Answer, AnswerBlock, AnswerPart, AnswerTerm
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


def bracket(number: int) -> str:
    """Mark a citation as ``[n]``, for plain text and for JSON."""
    return f"[{number}]"


def footnote(number: int) -> str:
    """Mark a citation as a Markdown footnote, `` [^n]``, after a space.

    Two plain markers side by side, ``[1][2]``, read in Markdown as a
    reference link. A footnote marker links to its definition instead.
    The space keeps a marker off the word before it and off the marker
    before it, which some renderers join into one.
    """
    return f" [^{number}]"


def spaced(number: int) -> str:
    """Mark a citation as `` [n]``, after a space, for a reply of a chat.

    A space keeps a marker off the word before it and off the marker
    before it, so Markdown reads ``[1] [2]`` as two markers and not as a
    reference link. The sources are a numbered list, which no marker
    links to.
    """
    return f" [{number}]"


def number_citations(
    answer: Answer,
    numbers: dict[Chunk, int] | None = None,
    mark: Callable[[int], str] = bracket,
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
            mark(numbers.setdefault(c, len(numbers) + 1)) for c in part.cites
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

    See ``answer_text``, which also returns the sources in the order of
    their numbers.
    """
    return answer_text(answer, question, link, base, kind_of)[0]


def answer_text(
    answer: Answer,
    question: str,
    link: Callable[[Chunk], str],
    base: Path | None = None,
    kind_of: Callable[[Chunk], str] | None = None,
    *,
    footnotes: bool = True,
) -> tuple[str, list[Chunk]]:
    """Return an answer as Markdown, and its sources in the order of their numbers.

    Open with the short answer. Give each part of the question its own
    section, a procedure as numbered steps, and registers or values as a
    table. Gather what the sources leave open in one block, so a reader
    sees what is not settled. Wrap text to ``WIDTH`` columns; a table, a
    link, and a directory stay whole. List each source as a link with its
    file name and lines, and below it what kind of file it is, its
    heading, and its directory. Name the acronyms the answer uses, and
    what each stands for, before the sources. For a question the
    documents do not cover, list the closest sources. With *footnotes*
    False, mark a citation as `` [n]`` and list the sources as a numbered
    list, as a chat shows them.
    """
    mark = footnote if footnotes else spaced
    numbers: dict[Chunk, int] = {}
    lines = answer_head(question)
    if answer.covered and answer.blocks:
        for block in answer.blocks:
            lines += block_lines(block, numbers, mark)
    elif answer.covered:
        text, _ = number_citations(answer, numbers, mark=mark)
        for paragraph in text.split("\n\n"):
            if paragraph.strip():
                lines += [_fill(paragraph), ""]
    tail, cited = answer_tail(answer, numbers, link, base, kind_of, footnotes=footnotes)
    return "\n".join(lines + tail), cited


def answer_head(question: str) -> list[str]:
    """Return the lines an answer starts with: the question as its heading."""
    return [f"# {_one_line(question)}", ""]


def block_lines(
    block: AnswerBlock, numbers: dict[Chunk, int], mark: Callable[[int], str]
) -> list[str]:
    """Return the lines of one block of an answer, numbering new citations.

    The numbers continue *numbers*, so blocks written one by one number
    their citations as the whole answer does.
    """
    return [*_render(block, numbers, mark), ""]


def answer_tail(
    answer: Answer,
    numbers: dict[Chunk, int],
    link: Callable[[Chunk], str],
    base: Path | None = None,
    kind_of: Callable[[Chunk], str] | None = None,
    *,
    footnotes: bool = True,
) -> tuple[list[str], list[Chunk]]:
    """Return the lines after the text of an answer, and its sources by number.

    Name the acronyms and list the sources of a covered answer, with the
    numbers its text gave them in *numbers*. For a question the documents
    do not cover, list the closest sources. End with the follow-up searches.
    """
    mark = footnote if footnotes else spaced
    lines: list[str] = []
    if answer.covered:
        lines += _render_terms(answer.terms, numbers, mark)
        cited = list(numbers)
        lines += ["## Sources", ""]
    else:
        cited = list(answer.sources)
        lines += [_fill(NOT_COVERED_TEXT, "> "), "", "## Closest sources", ""]
    # A footnote definition for a cited source, so each marker links to
    # it. A closest source is cited by nothing, so it is a plain list, as
    # each source is when the markers are no footnotes.
    defined = answer.covered and footnotes
    for n, chunk in enumerate(cited, 1):
        marker = f"[^{n}]: " if defined else f"{n}. "
        place = f"{chunk.path.name}{chunk.location.render()}"
        lines.append(f"{marker}[{place}]({link(chunk)})")
        # A footnote continues on lines indented by four spaces.
        indent = "    " if defined else " " * len(marker)
        about = [kind_of(chunk)] if kind_of else []
        # A heading path joins its levels with " > ". A wrapped line that
        # starts with ">" is a quote in Markdown, so a source line writes
        # the separator as " › ".
        heading = symbol_of(chunk).replace(" > ", " › ")
        lines.append(_fill(" · ".join([*about, heading]), indent))
        # A directory does not wrap: a path broken over two lines no longer
        # pastes into a shell.
        lines.append(f"{indent}{display_path(chunk.path.parent, base).as_posix()}/")
    if answer.searches:
        lines += ["", "## Follow-up searches", ""]
        lines += [_fill(query, "- ", "  ") for query in answer.searches]
    return lines, cited


def format_brief(answer: Answer) -> str:
    """Return the short answer alone, as plain text with no citations.

    A brief answer is for a reader who wants the fact and not the proof,
    such as a prompt or a script. The same question without brevity
    gives the sources. For a question the documents do not cover, say
    so in one line.
    """
    if not answer.covered:
        return NOT_COVERED_TEXT.split(".")[0] + "."
    summary = next((b for b in answer.blocks if b.kind == "summary"), None)
    parts = summary.parts if summary else answer.parts
    text = number_citations(
        Answer(parts=tuple(parts), covered=True, sources=()), mark=_unmarked
    )[0]
    return _fill(text)


def format_chat(
    answer: Answer,
    question: str,
    link: Callable[[Chunk], str],
    *,
    brief: bool,
    kind_of: Callable[[Chunk], str] | None = None,
) -> tuple[str, list[Chunk]]:
    """Return one reply of a chat as Markdown, and its sources by number.

    Mark a citation as `` [n]``, and list the sources as a numbered list
    of links. A brief reply is the short answer and one item for each
    source it cites, so a short reply stays checkable. A full reply is
    the answer as ``answer_text`` writes it. A question the sources do
    not cover says so in one line.
    """
    if not answer.covered:
        return NOT_COVERED_TEXT.split(".")[0] + ".", []
    if not brief:
        return answer_text(answer, question, link, kind_of=kind_of, footnotes=False)
    summary = next((b for b in answer.blocks if b.kind == "summary"), None)
    parts = summary.parts if summary else answer.parts
    text, cited = number_citations(
        Answer(parts=tuple(parts), covered=True, sources=()), mark=spaced
    )
    lines = [_fill(text)]
    if cited:
        lines.append("")
        for n, c in enumerate(cited, 1):
            place = f"{display_path(c.path)}:{c.start_line}-{c.end_line}"
            lines.append(f"{n}. [{place}]({link(c)})")
    return "\n".join(lines), cited


# The stages of an answer as a reader names them, in the order they run.
_STAGES = (
    ("searching", "search"),
    ("reading", "read"),
    ("choosing", "rerank"),
    ("writing", "answer"),
    ("following", "follow-up"),
)


def answer_usage(answer: Answer) -> dict[str, object]:
    """Return what an answer cost, as the data a server replies with.

    The keys are the ones the ``ask`` tool sends, and the ones
    ``format_usage`` reads.
    """
    return {
        "model": answer.model,
        "input_tokens": answer.input_tokens,
        "output_tokens": answer.output_tokens,
        "rerank_model": answer.rerank_model,
        "rerank_input_tokens": answer.rerank_input_tokens,
        "rerank_output_tokens": answer.rerank_output_tokens,
        "turns": answer.turns,
        "searches": list(answer.searches),
        "timings": {stage: round(seconds, 3) for stage, seconds in answer.timings},
        "seconds": round(answer.seconds, 3),
        "cost_usd": round(answer.cost_usd, 6),
    }


def format_usage(usage: Mapping[str, object], versions: str = "") -> str:
    """Return a table of where the seconds and tokens of an answer went.

    Give one row to each stage that ran: its time, and for the rerank and
    the answer the model and its tokens. End with the total. A stage with
    a model and no time gets its tokens alone. Close with *versions*,
    after a blank line, when it is given.
    """
    timings = usage.get("timings")
    timings = timings if isinstance(timings, Mapping) else {}
    rows = []
    for stage, label in _STAGES:
        model, read, wrote = _row(stage, usage)
        if stage in timings or model:
            spent = f"{_seconds(timings[stage]):.1f} s" if stage in timings else ""
            rows.append([label, spent, model, read, wrote])
    seconds = usage.get("seconds")
    cost = usage.get("cost_usd")
    # What claude says the answer would cost at the prices of the API.
    priced = f"about ${cost:.4f}" if isinstance(cost, (int, float)) and cost else ""
    if isinstance(seconds, (int, float)):
        rows.append(["total", f"{_seconds(seconds):.1f} s", priced, "", ""])
    tokens = any(row[3] or row[4] for row in rows)
    header = ["stage", "time", "model" if any(r[2] for r in rows) else "", "", ""]
    if tokens:
        header[3:] = ["in", "out"]
    table = [header, *rows]
    widths = [max(len(row[n]) for row in table) for n in range(5)]
    lines = [_aligned(row, widths) for row in table]
    if versions:
        lines += ["", versions]
    return "\n".join(lines)


def _aligned(row: list[str], widths: list[int]) -> str:
    """Return *row* in columns: names to the left, numbers to the right."""
    right = (False, True, False, True, True)
    cells = [
        cell.rjust(width) if numeric else cell.ljust(width)
        for cell, width, numeric in zip(row, widths, right, strict=True)
        if width
    ]
    return "  ".join(cells).rstrip()


def _row(stage: str, usage: Mapping[str, object]) -> tuple[str, str, str]:
    """Return the model, tokens read, and tokens written of a stage."""
    if stage == "choosing" and usage.get("rerank_model"):
        return (
            str(usage["rerank_model"]),
            f"{_count(usage.get('rerank_input_tokens')):,}",
            f"{_count(usage.get('rerank_output_tokens')):,}",
        )
    if stage == "writing" and usage.get("model"):
        turns = _count(usage.get("turns"))
        many = f", {turns} turns" if turns > 1 else ""
        return (
            f"{usage['model']}{many}",
            f"{_count(usage.get('input_tokens')):,}",
            f"{_count(usage.get('output_tokens')):,}",
        )
    if stage == "following":
        searches = usage.get("searches")
        count = len(searches) if isinstance(searches, list) else 0
        return (f"{count} searches" if count else "", "", "")
    return ("", "", "")


def _count(value: object) -> int:
    """Return *value* as a count, and 0 for anything that is not a number."""
    return int(value) if isinstance(value, (int, float)) else 0


def _seconds(value: object) -> float:
    """Return *value* as seconds, and 0 for anything that is not a number."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def _unmarked(number: int) -> str:
    return ""


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


def _marks(
    cites: Sequence[Chunk],
    numbers: dict[Chunk, int],
    mark: Callable[[int], str] = bracket,
) -> str:
    return "".join(mark(numbers.setdefault(c, len(numbers) + 1)) for c in cites)


def _joined(
    parts: Sequence[AnswerPart],
    numbers: dict[Chunk, int],
    mark: Callable[[int], str] = bracket,
) -> str:
    """Join parts into text with their markers, as ``number_citations`` does."""
    return number_citations(
        Answer(parts=tuple(parts), covered=True, sources=()), numbers, mark
    )[0]


def _render_terms(
    terms: Sequence[AnswerTerm],
    numbers: dict[Chunk, int],
    mark: Callable[[int], str],
) -> list[str]:
    """Render the acronyms of an answer as a list, each citing its source."""
    if not terms:
        return []
    lines = ["## Terms", ""]
    for term in terms:
        marks = _marks(term.cites, numbers, mark)
        lines.append(_fill(f"**{term.term}**: {term.meaning}{marks}", "- ", "  "))
    return [*lines, ""]


def terms_data(
    answer: Answer, cited: list[Chunk]
) -> tuple[list[dict[str, object]], list[Chunk]]:
    """Return the acronyms of an answer as plain data, and the cited chunks.

    Number a source that only a term cites after the ones *cited* already
    holds, as ``format_answer`` does, so ``[n]`` means one source in the
    text, the data, and the Markdown.
    """
    numbers = {chunk: n for n, chunk in enumerate(cited, 1)}
    data: list[dict[str, object]] = [
        {
            "term": term.term,
            "meaning": term.meaning,
            "cites": [numbers.setdefault(c, len(numbers) + 1) for c in term.cites],
        }
        for term in answer.terms
    ]
    return data, list(numbers)


def _render(
    block: AnswerBlock, numbers: dict[Chunk, int], mark: Callable[[int], str]
) -> list[str]:
    """Render one block as Markdown lines, numbering new citations in order."""
    lines = [f"### {block.heading}", ""] if block.heading else []
    if block.kind == "summary":
        return [_fill(f"**Short answer:** {_joined(block.parts, numbers, mark)}")]
    if block.kind == "gaps":
        lines = ["> **Not in the documents**", ">"]
        for part in block.parts:
            text = f"{part.text.strip()}{_marks(part.cites, numbers, mark)}"
            lines += _fill(text, "> - ").replace("\n> - ", "\n>   ").split("\n")
        return lines
    if block.kind == "steps":
        for n, part in enumerate(block.parts, 1):
            marker = f"{n}. "
            text = f"{marker}{part.text.strip()}{_marks(part.cites, numbers, mark)}"
            lines.append(_fill(text, "", " " * len(marker)))
        return lines
    if block.kind == "table":
        if block.parts:
            lines += [_fill(_joined(block.parts, numbers, mark)), ""]
        return lines + _table(block, numbers, mark)
    for paragraph in _joined(block.parts, numbers, mark).split("\n\n"):
        if paragraph.strip():
            lines += [_fill(paragraph), ""]
    return lines[:-1] if lines and lines[-1] == "" else lines


def _table(
    block: AnswerBlock, numbers: dict[Chunk, int], mark: Callable[[int], str]
) -> list[str]:
    """Render a table with a column of citations, padded to line up in a terminal."""
    width = max([len(block.columns)] + [len(r.cells) for r in block.rows])
    head = [*block.columns, *[""] * (width - len(block.columns)), "Sources"]
    body = [
        [*r.cells, *[""] * (width - len(r.cells)), _marks(r.cites, numbers, mark)]
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
