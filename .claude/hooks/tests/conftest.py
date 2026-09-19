"""Make the hooks importable as plain modules, the way Claude Code runs them."""

import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
