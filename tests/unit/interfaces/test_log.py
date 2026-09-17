"""Test logging configuration and the formatter."""

import logging
import sys

import pytest

from ish.interfaces.log import _Ansi, _DeltaFormatter, resolve_color, setup_logging


def record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="ish",
        level=level,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestSetup:
    def test_setup_logging_creates_handler(self) -> None:
        setup_logging(verbosity=0, color=False)

        root = logging.getLogger("ish")
        assert root.level == logging.WARNING
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert isinstance(handler.formatter, _DeltaFormatter)

    def test_verbosity_mapping(self) -> None:
        setup_logging(verbosity=1, color=False)
        assert logging.getLogger("ish").level == logging.INFO

        setup_logging(verbosity=2, color=False)
        assert logging.getLogger("ish").level == logging.DEBUG

    def test_handler_follows_a_replaced_stderr(self, capsys) -> None:
        """A handler pinned to the original stream would lose the record."""
        setup_logging(verbosity=0, color=False)
        logging.getLogger("ish.test").warning("visible message")
        assert "visible message" in capsys.readouterr().err

    def test_resolve_color_modes(self, monkeypatch) -> None:
        """Verify the tri-state color option resolves to a boolean."""
        assert resolve_color("always") is True
        assert resolve_color("never") is False

        monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
        assert resolve_color("auto") is True
        monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
        assert resolve_color("auto") is False


class TestFormatter:
    def test_without_color(self) -> None:
        output = _DeltaFormatter(use_color=False).format(
            record(logging.WARNING, "Test warning")
        )
        assert "[WAR] Test warning" in output
        assert _Ansi.YELLOW not in output

    def test_with_color(self) -> None:
        formatter = _DeltaFormatter(use_color=True)
        out_dbg = formatter.format(record(logging.DEBUG, "Test debug"))
        assert "Test debug" in out_dbg
        assert _Ansi.CYAN in out_dbg

        out_err = formatter.format(record(logging.ERROR, "Test err"))
        assert "Test err" in out_err
        assert _Ansi.RED in out_err

    @pytest.mark.parametrize("color", [True, False])
    def test_the_first_record_carries_the_clock(self, color: bool) -> None:
        out = _DeltaFormatter(use_color=color).format(record(logging.WARNING, "First"))
        assert "First" in out
        assert "+00:00" not in out
        assert (_Ansi.GREY in out) is color

    def test_later_records_carry_the_delta(self) -> None:
        formatter = _DeltaFormatter(use_color=False)
        formatter.format(record(logging.WARNING, "First"))
        assert "+00:00:00" in formatter.format(record(logging.WARNING, "Second"))

    def test_each_formatter_measures_from_its_own_start(self) -> None:
        """Two runs in one process must not share a clock."""
        first = _DeltaFormatter(use_color=False)
        first.format(record(logging.WARNING, "a"))
        second = _DeltaFormatter(use_color=False)
        assert "+00:00" not in second.format(record(logging.WARNING, "b"))

    @pytest.mark.parametrize(
        ("level", "color"),
        [
            (logging.CRITICAL, _Ansi.BOLD_RED),
            (logging.ERROR, _Ansi.RED),
            (logging.WARNING, _Ansi.YELLOW),
            (logging.INFO, ""),
            (logging.DEBUG, _Ansi.CYAN),
            (5, _Ansi.BLUE),
        ],
    )
    def test_every_level_has_a_color(self, level: int, color: str) -> None:
        assert color in _DeltaFormatter(use_color=True).format(record(level, "x"))

    @pytest.mark.parametrize(
        ("level", "tag"),
        [
            (logging.CRITICAL, "[FTL] "),
            (logging.ERROR, "[ERR] "),
            (logging.WARNING, "[WAR] "),
            (logging.INFO, "[INF] "),
            (logging.DEBUG, "[DBG] "),
            (5, "[TRC] "),
        ],
    )
    def test_every_level_has_a_tag(self, level: int, tag: str) -> None:
        assert tag in _DeltaFormatter(use_color=False).format(record(level, "x"))
