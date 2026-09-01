"""Test CLI logging configuration and formatting."""

import logging

from ish.interfaces.cli.log import _Ansi, _DeltaFormatter, setup_logging


def test_setup_logging_creates_handler():
    # Setup with verbosity 0 (WARNING)
    setup_logging(verbosity=0, color=False)

    root = logging.getLogger("ish")
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1

    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert isinstance(handler.formatter, _DeltaFormatter)


def test_verbosity_mapping():
    setup_logging(verbosity=1, color=False)
    assert logging.getLogger("ish").level == logging.INFO

    setup_logging(verbosity=2, color=False)
    assert logging.getLogger("ish").level == logging.DEBUG


def test_formatter_without_color():
    formatter = _DeltaFormatter(use_color=False)

    # We can mock a record to format
    record = logging.LogRecord(
        name="ish",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Test warning",
        args=(),
        exc_info=None,
    )

    output = formatter.format(record)
    assert "[WAR] Test warning" in output
    assert _Ansi.YELLOW not in output


def test_formatter_with_color():
    formatter = _DeltaFormatter(use_color=True)

    # Debug record
    record_dbg = logging.LogRecord(
        name="ish",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Test debug",
        args=(),
        exc_info=None,
    )

    out_dbg = formatter.format(record_dbg)
    assert "Test debug" in out_dbg
    assert _Ansi.CYAN in out_dbg

    # Error record
    record_err = logging.LogRecord(
        name="ish",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Test err",
        args=(),
        exc_info=None,
    )

    out_err = formatter.format(record_err)
    assert "Test err" in out_err
    assert _Ansi.RED in out_err


def test_resolve_color_modes(monkeypatch):
    """Verify the tri-state color option resolves to a boolean."""
    import sys

    from ish.interfaces.cli.log import resolve_color

    assert resolve_color("always") is True
    assert resolve_color("never") is False

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert resolve_color("auto") is True
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    assert resolve_color("auto") is False


def test_handler_follows_a_replaced_stderr(capsys):
    """A handler pinned to the original stream would lose the record."""
    import logging

    from ish.interfaces.cli.log import setup_logging

    setup_logging(verbosity=0, color=False)
    logging.getLogger("ish.test").warning("visible message")

    assert "visible message" in capsys.readouterr().err
