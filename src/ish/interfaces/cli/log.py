"""Logging setup for ish.

All output goes to stderr so stdout stays clean for piped output.
Verbosity 0 = silent (WARNING only, effectively nothing in normal use).
"""

import logging
import sys


class _Ansi:
    GREY = "\033[90m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD_RED = "\033[1;91m"
    RESET = "\033[0m"


class _DeltaFormatter(logging.Formatter):
    """Compact formatter: delta timestamp + colored message to stderr."""

    _first: float | None = None

    def __init__(self, use_color: bool = True) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self._color = use_color

    def _prefix(self, record: logging.LogRecord) -> str:
        if _DeltaFormatter._first is None:
            _DeltaFormatter._first = record.created
            ts = self.formatTime(record, self.datefmt)
            tag = f"{ts:>9s}"
            if self._color:
                return f"{_Ansi.GREY}{tag}{_Ansi.RESET}"
            return tag

        delta = record.created - _DeltaFormatter._first
        m, s = divmod(int(delta), 60)
        h, m = divmod(m, 60)
        tag = f"+{h:02d}:{m:02d}:{s:02d}"
        if self._color:
            return f"{_Ansi.GREY}{tag}{_Ansi.RESET}"
        return tag

    def _level_color(self, levelno: int) -> str:
        if levelno >= logging.CRITICAL:
            return _Ansi.BOLD_RED
        if levelno >= logging.ERROR:
            return _Ansi.RED
        if levelno >= logging.WARNING:
            return _Ansi.YELLOW
        if levelno >= logging.INFO:
            return ""
        if levelno >= logging.DEBUG:
            return _Ansi.CYAN
        return _Ansi.BLUE  # TRACE

    def _level_tag(self, levelno: int) -> str:
        if levelno >= logging.CRITICAL:
            return "[FTL] "
        if levelno >= logging.ERROR:
            return "[ERR] "
        if levelno >= logging.WARNING:
            return "[WAR] "
        if levelno >= logging.INFO:
            return "[INF] "
        if levelno >= logging.DEBUG:
            return "[DBG] "
        return "[TRC] "

    def format(self, record: logging.LogRecord) -> str:
        prefix = self._prefix(record)
        msg = record.getMessage()
        if self._color:
            color = self._level_color(record.levelno)
            reset = _Ansi.RESET if color else ""
            return f"{prefix} {color}{msg}{reset}"
        tag = self._level_tag(record.levelno)
        return f"{prefix} {tag}{msg}"


# Trace level (below DEBUG)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

_VERBOSITY_MAP: dict[int, int] = {
    0: logging.WARNING,
    1: logging.INFO,
    2: logging.DEBUG,
}


def resolve_color(mode: str) -> bool:
    """Decide whether to color log output.

    Treat ``auto`` as "color only when stderr is a terminal".
    """
    if mode == "auto":
        return sys.stderr.isatty()
    return mode == "always"


def setup_logging(
    verbosity: int = 0,
    color: bool = True,
) -> None:
    """Configure the ish logger. Call once at startup."""
    level = _VERBOSITY_MAP.get(verbosity, TRACE)

    # Reset the delta reference so each invocation measures from its own start.
    _DeltaFormatter._first = None

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_DeltaFormatter(use_color=color))

    root = logging.getLogger("ish")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
