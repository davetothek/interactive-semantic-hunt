"""Parse command-line arguments.

Build every option flag from ``ish.settings.Settings`` so the command line
and ``ish.toml`` always accept the same option set.
"""

import argparse
import os
from dataclasses import dataclass, fields
from pathlib import Path

from ish import bootstrap
from ish.settings import Settings, load_settings

# Options whose accepted values come from a registry rather than a literal.
_DYNAMIC_CHOICES = {"embedder": lambda: sorted(bootstrap.EMBEDDERS)}


def _is_path_syntax(value: str) -> bool:
    """Return True when *value* is written as a filesystem path.

    Recognize a separator, a relative or home prefix, or a ``.py`` suffix.
    """
    return (
        os.sep in value
        or value.startswith(("./", "../", "~", "."))
        or value.endswith(".py")
    )


def _describe_default(value: object) -> str:
    """Render a default value for help text."""
    if isinstance(value, tuple):
        return " ".join(value)
    return str(value)


def add_settings_options(parser: argparse.ArgumentParser) -> None:
    """Add one command-line flag for every configurable setting."""
    group = parser.add_argument_group("options (also settable in ish.toml)")
    for f in fields(Settings):
        meta = f.metadata
        flags = [meta.get("flag") or f"--{f.name.replace('_', '-')}"]
        if meta.get("flag"):
            # Keep the derived long flag alongside an explicit short one.
            flags.append(f"--{f.name.replace('_', '-')}")

        kwargs = dict(meta.get("cli", {}))
        if f.name in _DYNAMIC_CHOICES:
            kwargs["choices"] = _DYNAMIC_CHOICES[f.name]()

        default = _describe_default(f.default)
        help_text = meta["help"]
        if default:
            help_text = f"{help_text} (default: {default})"

        # Default to None so the settings loader can tell "not supplied"
        # from "supplied the same value as the default".
        group.add_argument(*flags, dest=f.name, default=None, help=help_text, **kwargs)


@dataclass(frozen=True, slots=True)
class CliArgs:
    """Hold the per-run inputs plus the resolved settings."""

    path: Path
    query: str
    interactive: bool
    settings: Settings

    @classmethod
    def from_args(cls, argv: list[str] | None = None) -> CliArgs:
        """Parse command-line arguments and return a typed data class."""
        parser = argparse.ArgumentParser(
            prog="ish",
            description="Discover and list semantic code chunks in source files.",
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
            "-i",
            "--interactive",
            action="store_true",
            help="Run the interactive TUI.",
        )
        add_settings_options(parser)

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

        overrides = {f.name: getattr(args, f.name, None) for f in fields(Settings)}
        path = Path(path_val).resolve()

        return cls(
            path=path,
            query=query_val,
            interactive=args.interactive,
            settings=load_settings(overrides, start=_search_root(path)),
        )


def _search_root(path: Path) -> Path:
    """Return the directory to search upward from for a project config."""
    return path if path.is_dir() else path.parent
