"""Define the ish option set — one source of truth for CLI flags and TOML keys.

Every field below is both a command-line option and a key in ``ish.toml``.
The CLI builds its parser from these fields, and the TOML loader accepts
exactly these names, so the two interfaces cannot drift apart.

Resolve options in this order, where later sources win::

    defaults < user config < project config < environment < command line

The path to a config file is the one name outside that set. A file
cannot name where to find itself, so ``--config`` and ``ISH_CONFIG``
are read here rather than declared as a field. A file named that way
stands in place of the project files, which the loader then does not
look for.
"""

import logging
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_DIRNAME = ".ish"
CONFIG_BASENAME = "config.toml"
# The older flat name, still read so an existing file keeps working.
CONFIG_FILENAME = "ish.toml"
ENV_PREFIX = "ISH_"

CONFIG_OPTION = "config"
"""Name the config file to read, as a flag and as an override key.

This is the one name that is not a field of ``Settings``. A file
cannot hold the path to itself, so the name must stay out of the
option set that the command line and the file share.
"""
CONFIG_ENV_VAR = ENV_PREFIX + CONFIG_OPTION.upper()
"""The environment variable that names a config file."""

DEFAULT_EMBEDDER = "ollama"
DEFAULT_IGNORE = (".git", ".venv", "venv", "__pycache__")


class ConfigError(Exception):
    """Raise when a configuration file cannot be read or understood."""


def _opt(help: str, *, scope: str = "index", flag: str | None = None, **cli: Any):
    """Describe one option for both the CLI and the TOML file.

    *scope* says whether an option decides what enters the index or only
    what a search returns. An index-scope option must never be settable
    per call, because the next refresh would prune whatever it excluded.

    Derive the long flag from the field name unless *flag* adds a short
    one. Every option reaches both interfaces — there is deliberately no
    way to declare one that only a config file accepts.

    Extra keywords describe how the command line accepts the option. The
    CLI interface interprets them; this module never imports a parser.
    """
    return {"help": help, "scope": scope, "flag": flag, "cli": cli}


@dataclass(frozen=True, slots=True)
class Settings:
    """Hold every configurable option, resolved from all sources."""

    embedder: str = field(
        default=DEFAULT_EMBEDDER,
        metadata=_opt("Embedding backend to use."),
    )
    model: str = field(
        default="",
        metadata=_opt("Override the backend model. Empty uses the backend default."),
    )
    limit: int = field(
        default=5,
        metadata=_opt("Maximum number of search results.", scope="query", type=int),
    )
    tui_limit: int = field(
        default=50,
        metadata=_opt("Maximum number of results in the TUI.", type=int),
    )
    ignore: tuple[str, ...] = field(
        default=DEFAULT_IGNORE,
        metadata=_opt("Directory names to skip.", nargs="+", metavar="DIR"),
    )
    include: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            "Index only paths matching these regular expressions.",
            nargs="+",
            metavar="REGEX",
        ),
    )
    exclude: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            "Never index a path matching these regular expressions.",
            nargs="+",
            metavar="REGEX",
        ),
    )
    lang: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            "Return results only from these languages.",
            scope="query",
            nargs="+",
            metavar="LANG",
        ),
    )
    under: str = field(
        default="",
        metadata=_opt(
            "Return results only from paths matching this expression.",
            scope="query",
            metavar="REGEX",
        ),
    )
    type_patterns: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            "Sort a path into a type, as 'type:regex'. First match wins.",
            nargs="+",
            metavar="TYPE:REGEX",
        ),
    )
    type: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            "Return results only of these kinds: code, doc, test, config.",
            scope="query",
            nargs="+",
            metavar="TYPE",
        ),
    )
    federate: bool = field(
        default=True,
        metadata=_opt(
            "Also search stored indexes of directories below the path.",
            action="boolean_optional",
        ),
    )
    plugins: bool = field(
        default=True,
        metadata=_opt(
            "Load parsers from the user configuration directory.",
            action="boolean_optional",
        ),
    )
    git: bool = field(
        default=True,
        metadata=_opt(
            "Skip files that git ignores.",
            action="boolean_optional",
        ),
    )
    languages: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            "Languages to parse. Empty enables every registered parser.",
            nargs="+",
            metavar="LANG",
        ),
    )
    format: str = field(
        default="plain",
        metadata=_opt(
            "Output shape. Use grep for an editor picker.",
            scope="query",
            choices=["plain", "grep"],
        ),
    )
    color: str = field(
        default="auto",
        metadata=_opt("Control log color.", choices=["auto", "always", "never"]),
    )
    verbosity: int = field(
        default=0,
        metadata=_opt(
            "Increase log detail. Repeat for more.",
            flag="-v",
            action="count",
        ),
    )
    cache_dir: str = field(
        default="",
        metadata=_opt(
            "Index directory. Empty uses the platform cache.",
            metavar="DIR",
        ),
    )
    no_hybrid: bool = field(
        default=False,
        metadata=_opt(
            "Rank by vector similarity alone, with no lexical matching.",
            scope="query",
            action="store_true",
        ),
    )
    no_cache: bool = field(
        default=False,
        metadata=_opt(
            "Index in memory only, leaving nothing on disk.",
            action="store_true",
        ),
    )
    refresh_seconds: int = field(
        default=30,
        metadata=_opt(
            "How often a resident server re-checks a tree for changes.",
            metavar="SECONDS",
        ),
    )
    # Index scope, although a theme decides nothing about what is
    # indexed. Scope answers one question: may a call override this?
    # The answer is no. Only the TUI draws, and it takes the theme once
    # at startup, so a per-call override would have nothing to apply it
    # to. The two buckets hold no third answer.
    tui_theme: str = field(
        default="",
        metadata=_opt(
            "Theme for the TUI. Empty uses the Textual default.",
            metavar="NAME",
        ),
    )
    tui_debounce_ms: int = field(
        default=120,
        metadata=_opt(
            "Wait this long after a keystroke before searching, in the TUI.",
            metavar="MS",
        ),
    )
    refresh: bool = field(
        default=False,
        metadata=_opt(
            "Bring every stored index at or below the path up to date first.",
            action="store_true",
        ),
    )
    reindex: bool = field(
        default=False,
        metadata=_opt(
            "Discard the stored index and build it again.",
            action="store_true",
        ),
    )


def option_names() -> tuple[str, ...]:
    """Return every valid option name, for the CLI and the TOML loader alike."""
    return tuple(f.name for f in fields(Settings))


def choices_of(name: str) -> tuple[str, ...]:
    """Return the values option *name* accepts, or nothing when any string will do.

    Let an interface describe an option from the one place that defines
    it, rather than repeating the list.
    """
    for f in fields(Settings):
        if f.name == name:
            return tuple(f.metadata["cli"].get("choices", ()))
    raise KeyError(name)


def query_scope_names() -> tuple[str, ...]:
    """Return the options an interface may accept for a single call.

    These narrow what a search returns. Everything else decides what
    enters the index, and letting a caller change that per call would
    make the next refresh prune whatever the call excluded.
    """
    return tuple(f.name for f in fields(Settings) if f.metadata.get("scope") == "query")


def _coerce(name: str, value: Any, default: Any) -> Any:
    """Convert *value* to the type of the field default.

    Raise ``ConfigError`` when the value cannot represent that type.
    """
    try:
        if isinstance(default, tuple):
            if isinstance(value, str):
                value = [part for part in value.split(",") if part]
            if not isinstance(value, Sequence) or isinstance(value, str | bytes):
                raise TypeError("expected a list of strings")
            return tuple(str(item) for item in value)
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if isinstance(default, int):
            return int(value)
        return str(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Option {name!r} has an invalid value {value!r}: {exc}"
        ) from exc


def _accept(source: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the known options from *raw*, reporting the rest to stderr."""
    known = {f.name: f.default for f in fields(Settings)}
    accepted: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            log.warning("Ignore unknown option %r in %s", key, source)
            continue
        accepted[key] = _coerce(key, value, known[key])
    return accepted


TOOL_TABLE = "tool"
"""The table other tools keep their own settings under."""


def _options_in(path: Path, raw: dict[str, Any]) -> Mapping[str, Any]:
    """Return the ish options in *raw*, wherever the file keeps them.

    A file shared with other tools holds the options under
    ``[tool.ish]``, where ``[tool.black]`` and ``[tool.ruff]`` also
    live. Read that table alone then. Pass over every other key at the
    top level, and every other table under ``tool``: they belong to
    another tool, and reporting them as unknown options would be wrong.

    A file with no ``tool.ish`` table is an ish file, so every key in it
    is an option, as before.
    """
    tool = raw.get(TOOL_TABLE)
    if tool is None:
        return raw
    if not isinstance(tool, dict):
        raise ConfigError(f"Cannot parse {path}: '{TOOL_TABLE}' is not a table")
    if "ish" not in tool:
        return raw
    options = tool["ish"]
    if not isinstance(options, dict):
        raise ConfigError(f"Cannot parse {path}: 'tool.ish' is not a table")
    return options


def _read_toml(path: Path) -> dict[str, Any]:
    """Read one config file. Return an empty mapping when it is absent.

    Treat anything that is not a file as absent, so a directory of that
    name is skipped rather than reported. A file that exists and cannot
    be read is still an error, because ignoring it would silently drop
    the settings it holds.
    """
    if path.exists() and not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Cannot parse {path}: {exc}") from exc

    log.debug("Read config from %s", path)
    return _accept(str(path), _options_in(path, raw))


def _read_named(path: Path) -> dict[str, Any]:
    """Read the config file the caller named.

    A file that is not there is an error here, unlike a file the loader
    looks for on its own. The caller named this one, so silence would
    hide a typed path and apply defaults instead.
    """
    if not path.is_file():
        raise ConfigError(f"Cannot read {path}: there is no such config file")
    return _read_toml(path)


def config_names(directory: Path) -> tuple[Path, ...]:
    """Return the config files to look for in *directory*, in order.

    Keep the settings for a tree in one place, beside anything else the
    tool leaves there. Read the older flat name second, so a file
    written before this still applies.
    """
    return (
        directory / CONFIG_DIRNAME / CONFIG_BASENAME,
        directory / CONFIG_FILENAME,
    )


def user_config_path() -> Path:
    """Return the user-level config path, honoring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    directory = Path(base) / "ish"
    for candidate in (directory / CONFIG_BASENAME, directory / CONFIG_FILENAME):
        if candidate.is_file():
            return candidate
    return directory / CONFIG_BASENAME


def find_project_config(start: Path) -> Path | None:
    """Return the nearest project config file at or above *start*."""
    found = project_configs(start)
    return found[-1] if found else None


def project_configs(start: Path) -> list[Path]:
    """Return every project config from *start* upward, outermost first.

    Apply them in that order, so a file beside a subtree settles only
    the keys it names and inherits the rest. Reading the nearest file
    alone would make a setting for one tree silently drop what the
    repository above it had already decided.
    """
    found: list[Path] = []
    for directory in [start, *start.parents]:
        for candidate in config_names(directory):
            if candidate.is_file():
                found.append(candidate)
                break
    found.reverse()
    return found


def _from_env(environ: Mapping[str, str]) -> dict[str, Any]:
    """Read options from ``ISH_*`` environment variables.

    Pass over ``ISH_CONFIG``. It names a file to read, so it is not an
    option, and reporting it as one unknown would be wrong.
    """
    raw = {
        key.removeprefix(ENV_PREFIX).lower(): value
        for key, value in environ.items()
        if key.startswith(ENV_PREFIX) and key != CONFIG_ENV_VAR
    }
    return _accept("the environment", raw)


def load_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    start: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Resolve settings from every source and return them.

    Apply *overrides* last, so command-line options win. Omit a key from
    *overrides* to leave the lower-precedence value in place.

    A ``config`` key in *overrides*, or ``ISH_CONFIG`` in the
    environment, names one file to read in place of the project files
    at and above *start*. The key wins over the variable, the way a
    flag wins over the environment everywhere else.
    """
    settings = Settings()
    start = start or Path.cwd()
    environ = environ if environ is not None else os.environ
    supplied = {k: v for k, v in (overrides or {}).items() if v is not None}
    named = supplied.pop(CONFIG_OPTION, None) or environ.get(CONFIG_ENV_VAR)

    settings = replace(settings, **_read_toml(user_config_path()))

    if named:
        settings = replace(settings, **_read_named(Path(named).expanduser()))
    else:
        # Outermost first, so the nearest file wins key by key.
        for project in project_configs(start):
            settings = replace(settings, **_read_toml(project))

    settings = replace(settings, **_from_env(environ))

    if supplied:
        settings = replace(settings, **_accept("the command line", supplied))

    return settings
