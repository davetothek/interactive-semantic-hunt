"""Test logging formatter over all levels."""

import logging

from ish.interfaces.cli.log import _Ansi, _DeltaFormatter


def test_all_log_levels_colored():
    formatter = _DeltaFormatter(use_color=True)

    levels = [
        (logging.CRITICAL, _Ansi.BOLD_RED),
        (logging.ERROR, _Ansi.RED),
        (logging.WARNING, _Ansi.YELLOW),
        (logging.INFO, ""),
        (logging.DEBUG, _Ansi.CYAN),
        (5, _Ansi.BLUE),  # TRACE
    ]

    for level, expected_color in levels:
        record = logging.LogRecord(
            name="ish",
            level=level,
            pathname="",
            lineno=0,
            msg=f"Test {level}",
            args=(),
            exc_info=None,
        )
        out = formatter.format(record)
        assert expected_color in out


def test_all_log_levels_uncolored():
    formatter = _DeltaFormatter(use_color=False)

    levels = [
        (logging.CRITICAL, "[FTL] "),
        (logging.ERROR, "[ERR] "),
        (logging.WARNING, "[WAR] "),
        (logging.INFO, "[INF] "),
        (logging.DEBUG, "[DBG] "),
        (5, "[TRC] "),
    ]

    for level, expected_tag in levels:
        record = logging.LogRecord(
            name="ish",
            level=level,
            pathname="",
            lineno=0,
            msg=f"Test {level}",
            args=(),
            exc_info=None,
        )
        out = formatter.format(record)
        assert expected_tag in out
