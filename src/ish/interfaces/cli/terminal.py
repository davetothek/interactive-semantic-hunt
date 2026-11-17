"""Show an answer on a terminal: rendered on request, and the grey beside it.

An answer is Markdown, and a terminal gets it as a pipe does, so a copy
from the terminal is the text. With ``render``, a terminal shows the
tables, lists, quotes, and links that the markup stands for.

Rich draws the answer. Import Rich inside ``rendered`` alone, so a search
and a plain answer never load it.
"""

import functools
import io
import logging
import re

log = logging.getLogger(__name__)

GREY = "\033[90m"
RESET = "\033[0m"

MAX_WIDTH = 120
"""The widest a rendered answer gets. A longer line of prose is hard to read."""

# The parser of Rich refuses a link to a file, and a local answer links
# each source to its file. So a file link is rendered under a scheme the
# parser accepts, and takes its own scheme back in the text.
_FILE = "file://"
_STAND_IN = "ish-file://"

# A source of an answer: a footnote definition, or an item of a numbered
# list of sources, that holds one link.
_SOURCE = re.compile(r"^(?:\[\^(\d+)\]:|(\d+)\.) (\[[^\]\n]+\]\((\S+)\))$")
# A code span, which stays as it is, or the marker of a source: a
# footnote, or a number in brackets after a space, which is no link.
_MARKER = re.compile(r"(`[^`\n]*`)|\[\^(\d+)\]|(?<!\S)\[(\d+)\](?!\()")

# Spaces in a style at the end of a line, which Rich adds to fill the
# width. A copied line would keep them.
_PADDING = re.compile(r"(?:\x1b\[[0-9;]*m *\x1b\[0m)+$| +(?=\x1b\[0m$)")

DEFAULT_THEME = "quiet"
"""The theme of a rendered answer when the settings name none."""

# The themes of a rendered answer: Rich styles of the Markdown, and the
# Pygments theme of a code block. A style without a background follows
# the terminal palette, so it reads on a light and a dark terminal.
# Every theme gives inline code a style of its own, because the default
# of Rich paints a black background, which is a stain on a light terminal.
# The plain themes use dim, not grey, because a chat shows its sources in
# grey, and they must stand out from the answer.
THEMES: dict[str, tuple[dict[str, str], str]] = {
    "quiet": (
        {
            "markdown.h1": "bold",
            "markdown.h2": "bold",
            "markdown.h3": "bold",
            "markdown.h4": "bold italic",
            "markdown.code": "cyan",
            "markdown.block_quote": "italic dim",
            "markdown.item.bullet": "dim",
            "markdown.item.number": "dim",
            "markdown.table.border": "dim",
            "markdown.table.header": "bold",
            "markdown.hr": "dim",
            "markdown.link": "blue",
            "markdown.link_url": "dim",
        },
        "ansi_dark",
    ),
    "accent": (
        {
            "markdown.h1": "bold blue",
            "markdown.h2": "bold blue",
            "markdown.h3": "bold",
            "markdown.h4": "italic",
            "markdown.code": "yellow",
            "markdown.block_quote": "italic dim",
            "markdown.item.bullet": "blue",
            "markdown.item.number": "blue",
            "markdown.table.border": "dim",
            "markdown.table.header": "bold blue",
            "markdown.hr": "dim",
            "markdown.link": "underline blue",
            "markdown.link_url": "dim",
        },
        "ansi_dark",
    ),
    # Fixed colors on a dark ground, near the dark style of glow.
    "glow": (
        {
            "markdown.h1": "bold #FFFDF5 on #5A56E0",
            "markdown.h2": "bold #39A9FF",
            "markdown.h3": "bold #39A9FF",
            "markdown.h4": "bold #39A9FF",
            "markdown.code": "#FF5F87 on #303030",
            "markdown.block_quote": "italic #A8A8A8",
            "markdown.item.bullet": "#5A56E0",
            "markdown.item.number": "#5A56E0",
            "markdown.table.border": "#585858",
            "markdown.table.header": "bold #39A9FF",
            "markdown.hr": "#585858",
            "markdown.link": "#00AF87",
            "markdown.link_url": "underline #585858",
        },
        "github-dark",
    ),
    # The styles of Rich itself.
    "rich": ({"markdown.code": "bold cyan"}, "ansi_dark"),
}

# The theme of the sources, when they stand back in grey.
_QUIET = dict.fromkeys(
    ("markdown.item.number", "markdown.link", "markdown.link_url"), "bright_black"
)


def grey(text: str) -> str:
    """Return *text* in grey, for a line beside the answer."""
    return f"{GREY}{text}{RESET}"


def rendered(
    markdown: str,
    *,
    width: int,
    color: bool,
    grey_sources: bool = False,
    theme: str = DEFAULT_THEME,
) -> str:
    """Return *markdown* as a terminal shows it, *width* columns wide.

    With *color*, style the text in the named *theme* and make each link
    open with a click.
    Without it, keep the layout alone and print each URL beside its label.
    With *color* and *grey_sources*, show each source in grey, so the
    answer stands out from them.
    """
    lines = _prepared(markdown, color)
    if not (color and grey_sources):
        return _drawn("\n".join(line for line, _ in lines), width, color, theme)
    # Draw each run of sources apart from the text around it. Markdown
    # puts a blank line between two blocks, so a run starts and ends one.
    runs: list[tuple[list[str], bool]] = []
    for line, source in lines:
        if runs and runs[-1][1] == source:
            runs[-1][0].append(line)
        else:
            runs.append(([line], source))
    drawn = [
        _drawn("\n".join(run).strip("\n"), width, color, theme, quiet=source).strip(
            "\n"
        )
        for run, source in runs
        if "".join(run).strip()
    ]
    return "\n\n".join(drawn)


def _theme(name: str) -> tuple[dict[str, str], str]:
    """Return the styles and the code theme of the theme *name*.

    Report a name no theme answers to, and use the default theme.
    """
    if name in THEMES:
        return THEMES[name]
    _warn_unknown(name)
    return THEMES[DEFAULT_THEME]


@functools.cache
def _warn_unknown(name: str) -> None:
    """Report the unknown theme *name* once, not once for each block."""
    log.warning(
        "There is no render theme named %s. Use one of: %s",
        name,
        ", ".join(THEMES),
    )


def _drawn(
    markdown: str, width: int, color: bool, theme: str, *, quiet: bool = False
) -> str:
    """Return *markdown* drawn by Rich in *theme*, in grey when *quiet*."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.theme import Theme

    out = io.StringIO()
    styles, code_theme = _theme(theme)
    styles = {**styles, **(_QUIET if quiet else {})}
    console = Console(
        file=out,
        width=width,
        force_terminal=True,
        color_system="auto" if color else None,
        no_color=not color,
        highlight=False,
        theme=Theme(styles),
    )
    text = markdown.replace(f"]({_FILE}", f"]({_STAND_IN}")
    console.print(
        Markdown(text, hyperlinks=color, code_theme=code_theme),
        style="bright_black" if quiet else "",
    )
    shown = out.getvalue().replace(_STAND_IN, _FILE).rstrip("\n")
    return "\n".join(_PADDING.sub("", line).rstrip() for line in shown.split("\n"))


def _prepared(markdown: str, linked: bool) -> list[tuple[str, bool]]:
    """Return the lines of *markdown* in a form that Rich reads.

    Rich knows no footnote. It reads a definition as the target of a link
    and the lines below it as code. So write each source as an item of a
    numbered list, with its details on lines of their own, and each marker
    as its number in brackets. When *linked*, the number opens the source.
    Pair each line with True when it belongs to a source.
    """
    lines = markdown.split("\n")
    found = [source for source in map(_SOURCE.match, lines) if source]
    urls = {source[1] or source[2]: source[4] for source in found}

    def marker(match: re.Match[str]) -> str:
        if match[1]:
            return match[1]
        number = match[2] or match[3]
        if linked and number in urls:
            return f"[\\[{number}\\]]({urls[number]})"
        return f"\\[{number}\\]"

    out: list[tuple[str, bool]] = []
    in_source = False
    for line in lines:
        source = _SOURCE.match(line)
        if source:
            out.append((f"{source[1] or source[2]}. {source[3]}", True))
            in_source = True
        elif in_source and line.startswith(" ") and line.strip():
            # A backslash ends the line above, so each detail keeps a line.
            out[-1] = (f"{out[-1][0]}\\", True)
            out.append((f"   {line.strip()}", True))
        else:
            in_source = False
            out.append((_MARKER.sub(marker, line), False))
    return out
