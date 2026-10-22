"""Name the directory where the processes of one user share files while they run.

``ish-serve`` writes its status file there, and a search leaves the marker
there that holds a document batch back. The directory follows the XDG
rule: ``$XDG_RUNTIME_DIR/ish``, or a directory of the user under the
temporary directory when no runtime directory is set.
"""

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

DIRNAME = "ish"


def runtime_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory the shared run-time files live in."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / DIRNAME
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return Path(tempfile.gettempdir()) / f"{DIRNAME}-{uid}"
