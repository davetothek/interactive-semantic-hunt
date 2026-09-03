import logging

from ish.interfaces.cli.log import _DeltaFormatter


def test_formatter_first_record_uncolored():
    # Reset the static first timestamp
    _DeltaFormatter._first = None

    formatter = _DeltaFormatter(use_color=False)
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
