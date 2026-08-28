import logging

from ish.interfaces.cli.log import _Ansi, _DeltaFormatter


def test_formatter_first_record_colored():
    # Reset the static first timestamp
    _DeltaFormatter._first = None

    formatter = _DeltaFormatter(use_color=True)
    record = logging.LogRecord(
        name="ish",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="First record",
        args=(),
        exc_info=None,
    )

    out = formatter.format(record)
    assert "First record" in out
    assert _Ansi.GREY in out
