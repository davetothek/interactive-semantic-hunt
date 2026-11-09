"""Color an answer in Markdown for a terminal, and keep every character.

An answer goes to a terminal to be read and to a file or an editor to be
kept. Rendering it, with rules for headings and boxes for quotes, would
make the copy that a reader takes from the terminal differ from the text.
So add styles alone: headings bold, the markup dim, links underlined and
clickable, the gaps the documents leave in yellow. Remove the escape codes
and the Markdown is what ``format_answer`` wrote, byte for byte.

A link becomes clickable through OSC 8, which a terminal that does not
know it ignores. The URL stays printed beside the label, so a copy keeps it.
"""

import re

BOLD = "\033[1m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREY = "\033[90m"
RESET = "\033[0m"

_HEADING = re.compile(r"^(#{1,6} )(.*)$")
_QUOTE = re.compile(r"^(>)( ?)(.*)$")
_FOOTNOTE = re.compile(r"^(\[\^\d+\]:)(.*)$")
_DETAIL = re.compile(r"^(    )(\S.*)$")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+|file://[^)\s]+)\)")
_STRONG = re.compile(r"\*\*([^*\n]+)\*\*")
_MARKER = re.compile(r"\[\^\d+\]")
_CODE = re.compile(r"`([^`\n]+)`")
_ESCAPE = re.compile(r"\033\[[0-9;]*m|\033\]8;;[^\033]*\033\\")


def highlight(markdown: str) -> str:
    """Return *markdown* with terminal styles, and no character changed."""
    return "\n".join(_line(line) for line in markdown.split("\n"))


def strip(text: str) -> str:
    """Return *text* without the escape codes ``highlight`` adds."""
    return _ESCAPE.sub("", text)


def grey(text: str) -> str:
    """Return *text* in grey, for a line beside the answer."""
    return f"{GREY}{text}{RESET}"


def _line(line: str) -> str:
    heading = _HEADING.match(line)
    if heading:
        marks, title = heading.groups()
        return f"{DIM}{marks}{RESET}{BOLD}{CYAN}{_inline(title, BOLD + CYAN)}{RESET}"
    quote = _QUOTE.match(line)
    if quote:
        mark, space, rest = quote.groups()
        body = _inline(rest, YELLOW)
        return f"{DIM}{mark}{RESET}{space}{YELLOW}{body}{RESET}"
    footnote = _FOOTNOTE.match(line)
    if footnote:
        label, rest = footnote.groups()
        return f"{DIM}{label}{RESET}{_inline(rest)}"
    detail = _DETAIL.match(line)
    if detail:
        indent, rest = detail.groups()
        return f"{indent}{GREY}{rest}{RESET}"
    if line.startswith("|"):
        return line.replace("|", f"{DIM}|{RESET}")
    return _inline(line)


def _inline(text: str, base: str = "") -> str:
    """Style the links, strong text, markers, and code spans of one line.

    *base* is the style the line returns to after each span, so a span
    inside a heading or a quote leaves the rest of it styled.
    """
    back = RESET + base

    def link(found: re.Match[str]) -> str:
        label, url = found.groups()
        opened = f"\033]8;;{url}\033\\"
        closed = "\033]8;;\033\\"
        return (
            f"{DIM}[{RESET}{opened}{UNDERLINE}{label}{RESET}{closed}{DIM}]({url}){back}"
        )

    text = _LINK.sub(link, text)
    text = _STRONG.sub(lambda m: f"{DIM}**{RESET}{BOLD}{m[1]}{back}{DIM}**{back}", text)
    text = _MARKER.sub(lambda m: f"{CYAN}{m[0]}{back}", text)
    return _CODE.sub(lambda m: f"{DIM}`{RESET}{CYAN}{m[1]}{back}{DIM}`{back}", text)
