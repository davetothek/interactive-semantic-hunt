"""Keep the index of a tree current, for every client that reads it.

``ish`` brings the index up to date in front of every answer, and the
MCP server does so on a thread of its own. One tree with an editor, an
agent, and a shell gets three processes walking the same files and
embedding the same text, and nothing runs at all until one of them
asks. ``ish-serve`` is the one process that fills the index. It
refreshes the tree as soon as it starts, then every ``refresh_seconds``
or when a client asks, and it answers no query. Every client reads the
index itself, finds the status file this process keeps written, and
leaves the refreshing to it. Without a server every client works as
before.
"""

import argparse
import logging
import math
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ish import bootstrap
from ish.application.ports.embedder import Embedder
from ish.application.progress import Progress
from ish.application.search import WARM_QUERY
from ish.interfaces import heartbeat
from ish.interfaces.busy import Pacer
from ish.interfaces.log import setup_logging
from ish.settings import Settings, load_settings

log = logging.getLogger("ish.serve")

# How often the server looks for a nudge while it waits for the next
# cycle. A picker asks when it opens, and a second is soon enough.
NUDGE_SECONDS = 1.0

# The least time between two writes of the status while progress comes
# in. Embedding reports every batch, so this bounds the writes.
WRITE_SECONDS = 0.5

# How long closing waits for the beat thread to notice.
STOP_SECONDS = 5.0


class Keeper:
    """Refresh one tree on a schedule, and keep its status file written."""

    def __init__(
        self,
        settings: Settings,
        root: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._root = root
        self._clock = clock
        self._repository, self._checkout = bootstrap.repository_identity(root)
        self.status_path = heartbeat.status_for(root)
        self._nudge_path = heartbeat.nudge_for(root)
        self._pacer = Pacer(clock)
        self._doing: str | None = None
        self._last_finished: float | None = None
        self._written = -math.inf
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._beat: threading.Thread | None = None
        self._embedder: Embedder | None = None

    def other(self) -> heartbeat.Status | None:
        """Return the status of another live server that reaches this tree, or None.

        Two shells may start a server at once, and the checkouts of one
        repository need one server between them. The second one finds
        the status of the first and leaves. A status whose server has
        died is taken over.
        """
        for found in heartbeat.statuses():
            if found.pid == os.getpid() or not found.fresh():
                continue
            if not heartbeat.alive(found.pid):
                continue
            if found.covers(self._root, self._repository):
                return found
        return None

    def start(self) -> None:
        """Claim the tree, and keep the status written on a thread.

        A refresh reports nothing while the model embeds one long
        batch, so the thread writes the file on its own clock and a
        client never reads a live server as dead.
        """
        self.write()
        self._beat = threading.Thread(
            target=self._keep_written, name="ish-heartbeat", daemon=True
        )
        self._beat.start()

    def close(self) -> None:
        """Stop the beat, and take the status file away."""
        self._stopping.set()
        if self._beat is not None:
            self._beat.join(timeout=STOP_SECONDS)
        heartbeat.remove(self.status_path)

    def _keep_written(self) -> None:
        while not self._stopping.wait(heartbeat.BEAT_SECONDS):
            self.write()

    def write(self) -> None:
        """Write the status now."""
        with self._lock:
            status = heartbeat.Status(
                tree=self._root,
                pid=os.getpid(),
                updated=time.time(),
                repository=self._repository,
                checkout=self._checkout,
                refresh_seconds=float(self._settings.refresh_seconds),
                refreshing=self._doing,
                last_finished=self._last_finished,
            )
            try:
                heartbeat.write(self.status_path, status)
            except OSError as exc:
                log.warning("Cannot write the status to %s: %s", self.status_path, exc)
            self._written = self._clock()

    def _note(self, step: Progress) -> None:
        """Record what the refresh is doing, and write it when enough time passed."""
        self._doing = self._pacer.describe(step)
        if self._clock() - self._written >= WRITE_SECONDS:
            self.write()

    def refresh(self) -> None:
        """Bring every index of the tree, and of its repository, up to date now.

        A worktree of the repository is kept current too, wherever it
        is, so one server serves the main checkout and its worktrees.
        """
        self._doing = "starting"
        self.write()
        try:
            bootstrap.refresh_indexes(
                self._settings, self._root, on_progress=self._note, checkouts=True
            )
            self._last_finished = time.time()
            self._warm()
        except Exception as exc:  # noqa: BLE001 - the server outlives one bad cycle
            log.warning("Cannot refresh %s: %s", self._root, exc)
        finally:
            self._doing = None
            self.write()

    def _warm(self) -> None:
        """Keep the model loaded, so the next query of a client pays no load.

        Report a failure at INFO and leave it. The next query meets the
        same failure and names it to the person waiting.
        """
        try:
            if self._embedder is None:
                self._embedder = bootstrap.build_embedder(self._settings)
            self._embedder.embed_query(WARM_QUERY)
        except Exception as exc:  # noqa: BLE001 - a query reports it
            log.info("Cannot warm the backend: %s", exc)

    def serve_forever(self) -> None:
        """Refresh now, then on the interval or when asked, until stopped.

        Stop when the tree itself is gone. A server that went on would
        prune every file from the index and write its status for ever.
        """
        while not self._stopping.is_set():
            if not self._root.is_dir():
                log.warning("The tree %s is gone. Stopping.", self._root)
                return
            self.refresh()
            self._wait()

    def _wait(self) -> None:
        """Wait for the interval, a nudge, or a stop, whichever comes first.

        An interval of zero waits for a nudge alone.
        """
        seconds = self._settings.refresh_seconds
        deadline = math.inf if seconds <= 0 else self._clock() + seconds
        while not self._stopping.is_set():
            if heartbeat.take_nudge(self._nudge_path):
                log.info("Asked to refresh %s now", self._root)
                return
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            self._stopping.wait(min(NUDGE_SECONDS, remaining))


def _exit_on_signal(signum: int, _frame: object) -> None:
    """Leave on SIGTERM the way an interrupt leaves, so the status file goes too."""
    raise KeyboardInterrupt(f"signal {signum}")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    """Read the lifecycle options. Everything else comes from the config."""
    parser = argparse.ArgumentParser(
        prog="ish-serve",
        description="Keep the index of a tree current for every client that reads it.",
    )
    parser.add_argument(
        "tree",
        nargs="?",
        default=".",
        help=(
            "The tree to keep current (default: the working directory). "
            "The server reads its config there."
        ),
    )
    parser.add_argument("--once", action="store_true", help="Refresh once and exit.")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Log more. Twice for debug output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the server. Return an exit code."""
    args = _parse(argv)
    root = Path(args.tree).expanduser().resolve()
    if not root.is_dir():
        print(f"ish-serve: {root} is not a directory", file=sys.stderr)
        return 1
    settings = load_settings(start=root)
    setup_logging(verbosity=max(settings.verbosity, args.verbose), color=False)

    keeper = Keeper(settings, root)
    other = keeper.other()
    if other is not None:
        log.info(
            "ish-serve %d already keeps %s current, which reaches %s",
            other.pid,
            other.tree,
            root,
        )
        return 0

    # A signal handler is a main-thread thing. A test runs this on
    # another thread and leaves without the handler.
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _exit_on_signal)
    keeper.start()
    log.info(
        "Keeping %s current every %s s. Status in %s",
        root,
        settings.refresh_seconds,
        keeper.status_path,
    )
    try:
        if args.once:
            keeper.refresh()
        else:
            keeper.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        keeper.close()
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
