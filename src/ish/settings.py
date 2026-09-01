"""Define the ish option set — one source of truth for CLI flags and TOML keys.

Every field below is both a command-line option and a key in ``ish.toml``.
The CLI builds its parser from these fields, and the TOML loader accepts
exactly these names, so the two interfaces cannot drift apart.

Resolve options in this order, where later sources win::

    defaults < user config < project config < environment < command line
"""

import logging
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_FILENAME = "ish.toml"
ENV_PREFIX = "ISH_"

DEFAULT_EMBEDDER = "ollama"
DEFAULT_IGNORE = (".git", ".venv", "venv", "__pycache__")


class ConfigError(Exception):
    """Raise when a configuration file cannot be read or understood."""


def _opt(help: str, *, flag: str | None = None, **cli: Any):
    """Describe one option for both the CLI and the TOML file.

    Derive the long flag from the field name unless *flag* adds a short
    one. Every option reaches both interfaces — there is deliberately no
    way to declare one that only a config file accepts.

    Extra keywords describe how the command line accepts the option. The
    CLI interface interprets them; this module never imports a parser.
    """
    return {"help": help, "flag": flag, "cli": cli}


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
        metadata=_opt("Maximum number of search results.", type=int),
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
            nargs="+",
            metavar="LANG",
        ),
    )
    under: str = field(
        default="",
        metadata=_opt(
            "Return results only from paths matching this expression.",
            metavar="REGEX",
        ),
    )
    federate: bool = field(
        default=True,
        metadata=_opt(
            "Also search stored indexes of directories below the path.",
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


def _read_toml(path: Path) -> dict[str, Any]:
    """Read one config file. Return an empty mapping when it is absent."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Cannot parse {path}: {exc}") from exc

    log.debug("Read config from %s", path)
    return _accept(str(path), raw)


def user_config_path() -> Path:
    """Return the user-level config path, honoring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ish" / CONFIG_FILENAME


def find_project_config(start: Path) -> Path | None:
    """Search *start* and its parents for a project config file."""
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _from_env(environ: Mapping[str, str]) -> dict[str, Any]:
    """Read options from ``ISH_*`` environment variables."""
    raw = {
        key.removeprefix(ENV_PREFIX).lower(): value
        for key, value in environ.items()
        if key.startswith(ENV_PREFIX)
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
    """
    settings = Settings()
    start = start or Path.cwd()
    environ = environ if environ is not None else os.environ

    settings = replace(settings, **_read_toml(user_config_path()))

    project = find_project_config(start)
    if project is not None:
        settings = replace(settings, **_read_toml(project))

    settings = replace(settings, **_from_env(environ))

    if overrides:
        supplied = {k: v for k, v in overrides.items() if v is not None}
        settings = replace(settings, **_accept("the command line", supplied))

    return settings
