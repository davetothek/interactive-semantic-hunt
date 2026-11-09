"""Give a search its turn ahead of the document batch of another process.

Ollama serves one embedding request at a time. A process that embeds
documents sends requests of a few texts back to back, and a query that
arrives between them waits for the request in flight. A client that
embeds a query leaves a marker under the runtime directory first. A
process that embeds documents reads the marker between requests and
waits while it is fresh. So a person typing in a picker waits for one
request at most, and for none after it, while the index run loses the
seconds the person was typing and nothing else.

Nothing connects the two processes. The marker is a file, and a marker
that cannot be written costs the wait it would have saved and nothing
else.
"""

import logging
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from ish.runtime import runtime_dir

log = logging.getLogger(__name__)

MARKER_NAME = "query"

# How long after a query a document batch stays out of the way. A picker
# embeds a query on every keystroke, so this is the pause in typing that
# lets the index run take the slot again.
HOLD_SECONDS = 2.0

# How often a waiting index run looks at the marker.
POLL_SECONDS = 0.1


class Turn:
    """The marker a query leaves, and the wait a document batch takes."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path = path
        self._clock = clock
        self._sleep = sleep

    def mark(self) -> None:
        """Say that somebody embeds a query now."""
        now = self._clock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.touch()
            os.utime(self.path, (now, now))
        except OSError as exc:
            log.debug("Cannot leave the query marker at %s: %s", self.path, exc)

    def held(self) -> bool:
        """Return True while a query was embedded within ``HOLD_SECONDS``."""
        try:
            written = self.path.stat().st_mtime
        except OSError:
            return False
        return self._clock() - written < HOLD_SECONDS

    def wait(self) -> float:
        """Wait while a query is in the way, and return the seconds waited."""
        waited = 0.0
        while self.held():
            self._sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        return waited


def default_turn(environ: Mapping[str, str] | None = None) -> Turn:
    """Return the turn every process of this user shares."""
    return Turn(runtime_dir(environ) / MARKER_NAME)
