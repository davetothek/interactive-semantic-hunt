"""Show an answer on a terminal: rendered on request, and the grey beside it.

An answer is Markdown, and a terminal gets it as a pipe does, so a copy
from the terminal is the text. With ``render``, a terminal shows the
tables, lists, quotes, and links that the markup stands for.

Rich draws the answer. Import Rich inside ``rendered`` alone, so a search
and a plain answer never load it.
"""

import io
import re

GREY = "\033[90m"
RESET = "\033[0m"

MAX_WIDTH = 120
"""The widest a rendered answer gets. A longer line of prose is hard to read."""

# The parser of Rich refuses a link to a file, and a local answer links
# each source to its file. So a file link is rendered under a scheme the
# parser accepts, and takes its own scheme back in the text.
_FILE = "file://"
_STAND_IN = "ish-file://"

# A source of an answer: a footnote definition, or an item of the closest
# sources, that holds one link.
_SOURCE = re.compile(r"^(?:\[\^(\d+)\]:|(\d+)\.) (\[[^\]\n]+\]\((\S+)\))$")
# A code span, which stays as it is, or the marker of a footnote.
_MARKER = re.compile(r"(`[^`\n]*`)|\[\^(\d+)\]")


def grey(text: str) -> str:
    """Return *text* in grey, for a line beside the answer."""
    return f"{GREY}{text}{RESET}"


def rendered(markdown: str, *, width: int, color: bool) -> str:
    """Return *markdown* as a terminal shows it, *width* columns wide.

    With *color*, style the text and make each link open with a click.
    Without it, keep the layout alone and print each URL beside its label.
    """
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.theme import Theme

    out = io.StringIO()
    console = Console(
        file=out,
        width=width,
        force_terminal=True,
        color_system="auto" if color else None,
        no_color=not color,
        highlight=False,
        # The default code style paints a black background, which reads
        # as a stain on a light terminal.
        theme=Theme({"markdown.code": "bold cyan"}),
    )
    text = _prepared(markdown, color).replace(f"]({_FILE}", f"]({_STAND_IN}")
    console.print(Markdown(text, hyperlinks=color, code_theme="ansi_dark"))
    shown = out.getvalue().replace(_STAND_IN, _FILE).rstrip("\n")
    return "\n".join(line.rstrip() for line in shown.split("\n"))


def _prepared(markdown: str, linked: bool) -> str:
    """Return *markdown* with its footnotes in a form that Rich reads.

    Rich knows no footnote. It reads a definition as the target of a link
    and the lines below it as code. So write each source as an item of a
    numbered list, with its details on lines of their own, and each marker
    as its number in brackets. When *linked*, the number opens the source.
    """
    lines = markdown.split("\n")
    found = [source for source in map(_SOURCE.match, lines) if source]
    urls = {source[1]: source[4] for source in found if source[1]}

    def marker(match: re.Match[str]) -> str:
        if match[1]:
            return match[1]
        number = match[2]
        if linked and number in urls:
            return f"[\\[{number}\\]]({urls[number]})"
        return f"\\[{number}\\]"

    out: list[str] = []
    in_source = False
    for line in lines:
        source = _SOURCE.match(line)
        if source:
            out.append(f"{source[1] or source[2]}. {source[3]}")
            in_source = True
        elif in_source and line.startswith(" ") and line.strip():
            # A backslash ends the line above, so each detail keeps a line.
            out[-1] += "\\"
            out.append(f"   {line.strip()}")
        else:
            in_source = False
            out.append(_MARKER.sub(marker, line))
    return "\n".join(out)
