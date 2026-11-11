"""Parse command-line arguments.

Build every option flag from ``ish.settings.Settings`` so the command line
and ``ish.toml`` always accept the same option set.
"""

import argparse
import os
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ish import bootstrap
from ish.settings import CONFIG_OPTION, SECTIONS, Settings, load_settings

# The commands of the search program. One of them as the first word says
# what the run does. A line with no command is a search, or a listing when
# it has no query.
SEARCH = "search"
ASK = "ask"
PICK = "pick"
COMMANDS = (SEARCH, ASK, PICK)

_USAGE = {
    "": "%(prog)s [options] [query] [path]\n       %(prog)s command ...",
    SEARCH: "%(prog)s [options] query [path]",
    ASK: "%(prog)s [options] question [path]",
    PICK: "%(prog)s [options] [path]",
}

_DESCRIPTION = {
    "": (
        "Search code and documents by meaning, or list what they hold.\n"
        "A command as the first word does one thing:\n"
        "\n"
        "  search QUERY [PATH]   Search, also for a query that names a command.\n"
        "  ask QUESTION [PATH]   Answer a question, with the sources it cites.\n"
        "  pick [PATH]           Open the interactive picker.\n"
        "  remote COMMAND ...    Search, or ask, the tree of a server.\n"
        "  mcp                   Serve the tools over MCP. Also: ish-mcp\n"
        "  serve [TREE]          Keep the indexes of a tree current. Also: ish-serve\n"
        "  complete TEXT [PATH]  Finish a filter word. Also: ish-complete\n"
        "\n"
        "`ish command --help` shows what a command accepts."
    ),
    SEARCH: (
        "Search code and documents by meaning. The first word is the query,\n"
        "also when it is the name of a command or is written as a path."
    ),
    ASK: (
        "Answer a question from the code and documents, in Markdown, with the\n"
        "sources it cites."
    ),
    PICK: "Open the interactive picker, and print the chunk it selects.",
}

_EPILOG = (
    "Every option in the sections above can also be set in a config file:\n"
    "~/.config/ish/config.toml, or .ish/config.toml in a tree, with the name\n"
    "written in snake_case."
)

# Options whose accepted values come from a registry rather than a literal.
_DYNAMIC_CHOICES = {
    "embedder": lambda: sorted(bootstrap.EMBEDDERS),
    "languages": lambda: _language_choices(),
    "lang": lambda: _language_choices(),
}


def _language_choices() -> list[str]:
    """Return the language names a flag accepts, aliases included."""
    return list(bootstrap.build_vocabulary(Settings()).spellings)


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


def add_settings_options(
    parser: argparse.ArgumentParser,
    names: Collection[str] | None = None,
    *,
    renamed: Mapping[str, str] | None = None,
    any_value: Collection[str] = (),
) -> None:
    """Add one command-line flag for every configurable setting, by section.

    Show the sections in the order ``SECTIONS`` gives, and the options of
    each in the order they are declared. With *names*, add only those
    settings, for a command that most settings do not reach. *renamed*
    gives a setting the flag that this command calls it by. A setting in
    *any_value* takes every value, where another machine decides which
    values are valid.
    """
    renamed = renamed or {}
    groups = {title: parser.add_argument_group(title) for title in SECTIONS}
    for f in fields(Settings):
        if names is not None and f.name not in names:
            continue
        meta = f.metadata
        group = groups[meta["section"]]
        derived = f"--{f.name.replace('_', '-')}"
        flags = [renamed.get(f.name) or meta.get("flag") or derived]
        if meta.get("flag"):
            # Keep the derived long flag alongside an explicit short one.
            flags.append(derived)

        kwargs = dict(meta.get("cli", {}))
        # The settings module names this action rather than importing a
        # parser, so translate it here.
        if kwargs.get("action") == "boolean_optional":
            kwargs["action"] = argparse.BooleanOptionalAction
        if f.name in _DYNAMIC_CHOICES and f.name not in any_value:
            kwargs["choices"] = _DYNAMIC_CHOICES[f.name]()

        default = _describe_default(f.default)
        help_text = meta["help"]
        if default:
            help_text = f"{help_text} (default: {default})"

        # Default to None so the settings loader can tell "not supplied"
        # from "supplied the same value as the default".
        group.add_argument(*flags, dest=f.name, default=None, help=help_text, **kwargs)


def build_parser(command: str = "") -> argparse.ArgumentParser:
    """Return the parser for the whole command line of *command*.

    Keep every flag here, so one reader sees what the command accepts
    and a test can hold the parser to the option set. The line with no
    command also takes the flags that ``ask`` and ``pick`` replace.
    """
    parser = argparse.ArgumentParser(
        prog=f"ish {command}" if command else "ish",
        usage=_USAGE[command],
        description=_DESCRIPTION[command],
        epilog=_EPILOG,
        # Keep the lines of the command table. The help of each option
        # still wraps to the width of the terminal.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    if command == SEARCH:
        parser.add_argument("query", help="Semantic search query.")
    elif command == ASK:
        parser.add_argument("query", metavar="question", help="The question.")
        parser.add_argument(
            "--brief",
            action="store_true",
            help="Answer in a sentence or two, with no sources.",
        )
    elif not command:
        parser.add_argument(
            "query",
            nargs="?",
            default="",
            help="Semantic search query. Leave empty to scan and output all chunks.",
        )
        parser.add_argument(
            "-i",
            "--interactive",
            action="store_true",
            help="Run the interactive TUI, as `ish pick` does.",
        )
        parser.add_argument(
            "--ask",
            action="store_true",
            help="Answer the query as a question. `ish ask` replaces this flag.",
        )
        parser.add_argument(
            "--brief",
            action="store_true",
            help="Answer in a sentence or two. `ish ask --brief` replaces this flag.",
        )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Root path to scan (default: current directory).",
    )
    # Not one of the settings, and deliberately so: a config file cannot
    # hold the path to itself. See CONFIG_OPTION in ish.settings.
    parser.add_argument(
        "-c",
        f"--{CONFIG_OPTION}",
        dest=CONFIG_OPTION,
        default=None,
        metavar="PATH",
        help="Read options from this file instead of searching upward for one.",
    )
    add_settings_options(parser)
    parser.add_argument(
        "-V",
        "--version",
        action=_VersionAction,
        help="show program's version number and exit",
    )
    return parser


class _VersionAction(argparse.Action):
    """Print the version and exit.

    Read the package metadata only when the flag is given. Every run
    builds the parser, and the metadata costs a tenth of a warm query.
    """

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        super().__init__(
            option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        print(f"ish {installed_version()}")
        parser.exit()


def installed_version() -> str:
    """Return the installed version, or a word that says it is unknown."""
    import importlib.metadata

    try:
        return importlib.metadata.version("interactive-semantic-hunt")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True, slots=True)
class CliArgs:
    """Hold the per-run inputs plus the resolved settings."""

    path: Path
    query: str
    interactive: bool
    settings: Settings
    ask: bool = False
    brief: bool = False
    # The command the line starts with, or nothing for a line with none.
    command: str = ""
    # The flags as given, so a tree below can resolve its own
    # configuration and still let a flag override it.
    overrides: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_args(cls, argv: list[str] | None = None) -> "CliArgs":
        """Parse command-line arguments and return a typed data class.

        Read the first word as a command when it is one. The words after
        it are the arguments of that command.
        """
        words = list(sys.argv[1:] if argv is None else argv)
        command = words[0] if words and words[0] in COMMANDS else ""
        args = build_parser(command).parse_args(words[1:] if command else words)

        # Positional resolution: `ish src/` binds "src/" to `query`, so move
        # a value to `path` when it is written as a path and exists on disk.
        # A bare word such as `ish log` stays a query even when a directory
        # of that name exists. A command names its positionals, so nothing
        # moves there.
        query_val = getattr(args, "query", "")
        path_val = args.path

        if (
            not command
            and query_val
            and path_val == "."
            and _is_path_syntax(query_val)
            and Path(query_val).exists()
        ):
            path_val = query_val
            query_val = ""
        # `ish --forget PATH` names a tree that is usually gone, which the
        # test above cannot move. Neither a forget nor a sweep has a
        # query, so the one positional is the path.
        maintenance = getattr(args, "forget", None) or getattr(args, "sweep", None)
        if maintenance and query_val and path_val == ".":
            path_val, query_val = query_val, ""

        overrides = {f.name: getattr(args, f.name, None) for f in fields(Settings)}
        # The file to read travels with the flags, so a tree below
        # resolves its settings from the same file the caller named.
        overrides[CONFIG_OPTION] = args.config
        path = Path(path_val).resolve()

        brief = getattr(args, "brief", False)
        return cls(
            path=path,
            query=query_val,
            interactive=command == PICK or getattr(args, "interactive", False),
            ask=command == ASK or brief or getattr(args, "ask", False),
            brief=brief,
            command=command,
            settings=load_settings(overrides, start=_search_root(path)),
            overrides=overrides,
        )


def _search_root(path: Path) -> Path:
    """Return the directory to search upward from for a project config."""
    return path if path.is_dir() else path.parent
