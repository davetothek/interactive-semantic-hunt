"""CLI configuration parsing and models."""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from ish import bootstrap


def _is_path_syntax(value: str) -> bool:
    """Return True when *value* is written as a filesystem path.

    Recognize a separator, a relative or home prefix, or a ``.py`` suffix.
    """
    return (
        os.sep in value
        or value.startswith(("./", "../", "~", "."))
        or value.endswith(".py")
    )


@dataclass(frozen=True, slots=True)
class CliArgs:
    """Runtime arguments parsed from the CLI."""

    path: Path
    query: str
    embedder: str
    interactive: bool
    verbosity: int
    color: bool

    @classmethod
    def from_args(cls, argv: list[str] | None = None) -> CliArgs:
        """Parse command-line arguments and return a typed data class."""
        parser = argparse.ArgumentParser(
            prog="ish",
            description="Discover and list semantic code chunks in Python files.",
        )
        parser.add_argument(
            "query",
            nargs="?",
            default="",
            help="Semantic search query. Leave empty to scan and output all chunks.",
        )
        parser.add_argument(
            "path",
            nargs="?",
            default=".",
            help="Root path to scan (default: current directory).",
        )
        parser.add_argument(
            "--embedder",
            choices=sorted(bootstrap.EMBEDDERS),
            default=bootstrap.DEFAULT_EMBEDDER,
            help=(
                "Which embedding backend to use "
                f"(default: {bootstrap.DEFAULT_EMBEDDER})."
            ),
        )
        parser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help="Increase logging verbosity (e.g., -v for INFO, -vv for DEBUG).",
        )
        parser.add_argument(
            "-i",
            "--interactive",
            action="store_true",
            help="Run the interactive TUI.",
        )
        parser.add_argument(
            "--color",
            choices=["auto", "always", "never"],
            default="auto",
            help="Control log coloring (default: auto).",
        )

        import importlib.metadata

        try:
            version = importlib.metadata.version("interactive-semantic-hunt")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"

        parser.add_argument(
            "-V",
            "--version",
            action="version",
            version=f"%(prog)s {version}",
        )

        args = parser.parse_args(argv)

        # Positional resolution: `ish src/` binds "src/" to `query`, so move
        # a value to `path` when it is written as a path and exists on disk.
        # A bare word such as `ish log` stays a query even when a directory
        # of that name exists.
        query_val = args.query
        path_val = args.path

        if (
            query_val
            and path_val == "."
            and _is_path_syntax(query_val)
            and Path(query_val).exists()
        ):
            path_val = query_val
            query_val = ""

        if args.color == "auto":
            import sys

            use_color = sys.stderr.isatty()
        else:
            use_color = args.color == "always"

        return cls(
            path=Path(path_val).resolve(),
            query=query_val,
            embedder=args.embedder,
            interactive=args.interactive,
            verbosity=args.verbose,
            color=use_color,
        )
