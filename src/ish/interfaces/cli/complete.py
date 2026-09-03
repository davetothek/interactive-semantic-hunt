"""Complete a query from the command line, for an editor to call.

Print the completed query and nothing else, so a picker can put the
output straight back into its input. With ``--candidates``, print what
the word could still become instead, for a picker to show beside it.
"""

import sys
from pathlib import Path

from ish.interfaces.complete import candidates, complete
from ish.settings import Settings, load_settings

# How many choices to name before the line stops being readable.
MOST_SHOWN = 12


def main(argv: list[str] | None = None) -> int:
    """Print the completion of the query given. Return an exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    listing = "--candidates" in arguments
    rest = [item for item in arguments if item != "--candidates"]
    text = rest[0] if rest else ""
    root = Path(rest[1]).expanduser().resolve() if len(rest) > 1 else Path.cwd()

    try:
        settings = load_settings(start=root)
        sys.stdout.write(
            _offered(text, settings, root)
            if listing
            else complete(text, settings, root)
        )
    except Exception:
        # A completion that fails must leave the query as it was.
        sys.stdout.write("" if listing else text)
        return 1
    return 0


def _offered(text: str, settings: Settings, root: Path) -> str:
    """Render the choices for a word that cannot be finished yet."""
    choices = candidates(text, settings, root)
    if not choices:
        return ""
    shown = "  ".join(choices[:MOST_SHOWN])
    if len(choices) > MOST_SHOWN:
        shown += f"  ... and {len(choices) - MOST_SHOWN} more"
    return shown
