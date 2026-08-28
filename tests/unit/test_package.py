"""Test the package root and the layer boundary it must respect."""

import subprocess
import sys

from ish.interfaces.python.api import Ish


def test_ish_instantiates() -> None:
    """Confirm the skeleton API class can be constructed."""
    instance = Ish()
    assert isinstance(instance, Ish)


def test_package_root_imports_no_layers() -> None:
    """Confirm ``import ish`` pulls in no layer modules.

    Run in a fresh interpreter so modules imported by other tests
    cannot mask a violation.
    """
    code = (
        "import ish, sys; "
        "layers = ('ish.interfaces', 'ish.application', "
        "'ish.adapters', 'ish.bootstrap'); "
        "bad = [m for m in sys.modules if m.startswith(layers)]; "
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
