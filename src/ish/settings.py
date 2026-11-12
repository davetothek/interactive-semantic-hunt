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

MCP_TOKEN_ENV_VAR = ENV_PREFIX + "MCP_TOKEN"
# The password of the LDAP service account, or a file that holds it, as
# Docker keeps a secret. Neither is an option, so no config file holds it.
LDAP_PASSWORD_ENV_VAR = ENV_PREFIX + "LDAP_BIND_PASSWORD"
LDAP_PASSWORD_FILE_ENV_VAR = LDAP_PASSWORD_ENV_VAR + "_FILE"
"""The environment variable that holds the bearer token of MCP over HTTP.

It is a secret, not an option. A secret must not sit in a config file
or show as a flag, so the name stays out of the option set.
"""

REMOTE_ENV_VAR = ENV_PREFIX + "REMOTE"
"""The environment variable that names the server of ``ish remote``.

It is the ``remote`` setting, read as every setting is. The name is here
for an error that tells the user to set it.
"""

DEFAULT_EMBEDDER = "ollama"
DEFAULT_IGNORE = (".git", ".venv", "venv", "__pycache__")


class ConfigError(Exception):
    """Raise when a configuration file cannot be read or understood."""


# The sections of the help, in the order a reader looks for them. Each
# option names its section where it is declared, so a new option takes
# one line and no edit to the command line interface.
SEARCH = "search"
ANSWERS = "answers (--ask, --brief)"
OUTPUT = "output"
INDEXED = "what enters the index"
EMBEDDING = "embedding"
STORAGE = "where the index lives"
MAINTENANCE = "maintenance"
PICKER = "interactive picker (-i)"
SERVER = "server over HTTP (ish mcp --http)"
SECTIONS = (
    SEARCH,
    ANSWERS,
    OUTPUT,
    INDEXED,
    EMBEDDING,
    STORAGE,
    MAINTENANCE,
    PICKER,
    SERVER,
)


def _opt(
    section: str,
    help: str,
    *,
    scope: str = "index",
    flag: str | None = None,
    **cli: Any,
):
    """Describe one option for both the CLI and the TOML file.

    *section* is the part of the help the option belongs to, one of
    ``SECTIONS``. *scope* says whether an option decides what enters the index or only
    what a search returns. An index-scope option must never be settable
    per call, because the next refresh would prune whatever it excluded.

    Derive the long flag from the field name unless *flag* adds a short
    one. Every option reaches both interfaces — there is deliberately no
    way to declare one that only a config file accepts.

    Extra keywords describe how the command line accepts the option. The
    CLI interface interprets them; this module never imports a parser.
    """
    return {"section": section, "help": help, "scope": scope, "flag": flag, "cli": cli}


@dataclass(frozen=True, slots=True)
class Settings:
    """Hold every configurable option, resolved from all sources."""

    embedder: str = field(
        default=DEFAULT_EMBEDDER,
        metadata=_opt(EMBEDDING, "Embedding backend to use."),
    )
    model: str = field(
        default="",
        metadata=_opt(
            EMBEDDING, "Override the backend model. Empty uses the backend default."
        ),
    )
    limit: int = field(
        default=5,
        metadata=_opt(
            SEARCH, "Maximum number of search results.", scope="query", type=int
        ),
    )
    tui_limit: int = field(
        default=50,
        metadata=_opt(PICKER, "Maximum number of results in the TUI.", type=int),
    )
    ignore: tuple[str, ...] = field(
        default=DEFAULT_IGNORE,
        metadata=_opt(INDEXED, "Directory names to skip.", nargs="+", metavar="DIR"),
    )
    include: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            INDEXED,
            "Index only paths matching these regular expressions.",
            nargs="+",
            metavar="REGEX",
        ),
    )
    exclude: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            INDEXED,
            "Never index a path matching these regular expressions.",
            nargs="+",
            metavar="REGEX",
        ),
    )
    lang: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            SEARCH,
            "Return results only from these languages.",
            scope="query",
            nargs="+",
            metavar="LANG",
        ),
    )
    under: str = field(
        default="",
        metadata=_opt(
            SEARCH,
            "Return results only from paths matching this expression.",
            scope="query",
            metavar="REGEX",
        ),
    )
    type_patterns: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            INDEXED,
            "Sort a path into a type, as 'type:regex'. First match wins.",
            nargs="+",
            metavar="TYPE:REGEX",
        ),
    )
    type: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            SEARCH,
            "Return results only of these kinds: code, doc, test, config.",
            scope="query",
            nargs="+",
            metavar="TYPE",
        ),
    )
    federate: bool = field(
        default=True,
        metadata=_opt(
            SEARCH,
            "Also search stored indexes of directories below the path.",
            action="boolean_optional",
        ),
    )
    plugins: bool = field(
        default=True,
        metadata=_opt(
            INDEXED,
            "Load parsers from the user configuration directory.",
            action="boolean_optional",
        ),
    )
    git: bool = field(
        default=True,
        metadata=_opt(
            INDEXED,
            "Skip files that git ignores.",
            action="boolean_optional",
        ),
    )
    unignore: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            INDEXED,
            "Index paths matching these expressions although git ignores them. "
            "Anchored at the tree root, like include.",
            nargs="+",
            metavar="REGEX",
        ),
    )
    context_tokens: int = field(
        default=2048,
        metadata=_opt(
            INDEXED,
            "How many tokens the embedding model reads of each chunk. The chunk "
            "cap follows from it. Changing it embeds every chunk again.",
            type=int,
            metavar="N",
        ),
    )
    max_chunks: int = field(
        default=1000,
        metadata=_opt(
            INDEXED,
            "Index a file that yields more chunks than this as one chunk. "
            "A file that yields thousands is generated.",
            type=int,
            metavar="N",
        ),
    )
    languages: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            INDEXED,
            "Languages to parse. Empty enables every registered parser.",
            nargs="+",
            metavar="LANG",
        ),
    )
    format: str = field(
        default="plain",
        metadata=_opt(
            OUTPUT,
            "Output shape. Use grep for an editor picker.",
            scope="query",
            choices=["plain", "grep"],
        ),
    )
    color: str = field(
        default="auto",
        metadata=_opt(
            OUTPUT,
            "Color the logs, the timing table, and a rendered answer: auto "
            "colors what goes to a terminal unless NO_COLOR is set.",
            choices=["auto", "always", "never"],
            metavar="WHEN",
        ),
    )
    render: bool = field(
        default=False,
        metadata=_opt(
            OUTPUT,
            "Render the answer of --ask on a terminal: tables, lists, and "
            "links in place of the Markdown. A pipe still gets the Markdown.",
            action="boolean_optional",
        ),
    )
    verbosity: int = field(
        default=0,
        metadata=_opt(
            OUTPUT,
            "Increase log detail. Repeat for more.",
            flag="-v",
            action="count",
        ),
    )
    cache_dir: str = field(
        default="",
        metadata=_opt(
            STORAGE,
            "Index directory. Empty uses the platform cache.",
            metavar="DIR",
        ),
    )
    no_hybrid: bool = field(
        default=False,
        metadata=_opt(
            SEARCH,
            "Rank by vector similarity alone, with no lexical matching.",
            scope="query",
            action="store_true",
        ),
    )
    no_cache: bool = field(
        default=False,
        metadata=_opt(
            STORAGE,
            "Index in memory only, leaving nothing on disk.",
            action="store_true",
        ),
    )
    refresh_seconds: int = field(
        default=30,
        metadata=_opt(
            STORAGE,
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
            PICKER,
            "Theme for the TUI. Empty uses the Textual default.",
            metavar="NAME",
        ),
    )
    tui_debounce_ms: int = field(
        default=120,
        metadata=_opt(
            PICKER,
            "Wait this long after a keystroke before searching, in the TUI.",
            metavar="MS",
        ),
    )
    link_base: str = field(
        default="",
        metadata=_opt(
            ANSWERS,
            "Base URL where the tree is published. Empty links to files on disk.",
            metavar="URL",
        ),
    )
    remote: str = field(
        default="",
        metadata=_opt(
            ANSWERS,
            "URL of the MCP server that `ish remote` searches. Also: ISH_REMOTE. "
            "A config file in a tree cannot set it.",
            metavar="URL",
        ),
    )
    # Index scope, so that no call can override them. These three decide
    # where the text of a document may go. Only the operator of a server
    # sets them, in the config file or on the command line.
    language_model: str = field(
        default="",
        metadata=_opt(
            ANSWERS,
            "Language model backend that writes answers. Empty turns answers off.",
            metavar="NAME",
        ),
    )
    answer_model: str = field(
        default="",
        metadata=_opt(
            ANSWERS,
            "Model the language model backend runs. Empty uses its default.",
            metavar="MODEL",
        ),
    )
    rerank_model: str = field(
        default="",
        metadata=_opt(
            ANSWERS,
            "Model of the same backend that picks which chunks an answer reads, "
            "from a wider search. Empty reads the best chunks as ranked.",
            metavar="MODEL",
        ),
    )
    rerank_candidates: int = field(
        default=50,
        metadata=_opt(
            ANSWERS,
            "Chunks a search returns for the rerank model to choose from.",
            type=int,
            metavar="N",
        ),
    )
    ask_files: int = field(
        default=32,
        metadata=_opt(
            ANSWERS,
            "Files an answer keeps parsed between questions. A server that "
            "answers from one tree can keep every file of it.",
            type=int,
            metavar="N",
        ),
    )
    ask_warm: bool = field(
        default=False,
        metadata=_opt(
            ANSWERS,
            "Parse the files of the tree when ish-mcp starts, up to "
            "ask_files, so the first answers read no file.",
            action="boolean_optional",
        ),
    )
    ask_turns: int = field(
        default=0,
        metadata=_opt(
            ANSWERS,
            "Follow-up searches the model may ask for before it answers. "
            "0 answers from the first search alone.",
            scope="query",
            type=int,
            metavar="N",
        ),
    )
    prompt_dir: str = field(
        default="",
        metadata=_opt(
            ANSWERS,
            "Directory of instruction files that replace the ones a language "
            "model receives, file by file. Empty uses the shipped ones.",
            metavar="DIR",
        ),
    )
    allowed_language_models: tuple[str, ...] = field(
        default=(),
        metadata=_opt(
            ANSWERS,
            "Language model backends that may receive text. Empty allows none.",
            nargs="+",
            metavar="NAME",
        ),
    )
    refresh: bool = field(
        default=False,
        metadata=_opt(
            MAINTENANCE,
            "Bring every stored index at or below the path up to date first.",
            action="store_true",
        ),
    )
    reindex: bool = field(
        default=False,
        metadata=_opt(
            MAINTENANCE,
            "Discard the stored index and build it again.",
            action="store_true",
        ),
    )
    forget: bool = field(
        default=False,
        metadata=_opt(
            MAINTENANCE,
            "Remove the stored index of the path and stop. The vectors stay.",
            action="store_true",
        ),
    )
    sweep: bool = field(
        default=False,
        metadata=_opt(
            MAINTENANCE,
            "Delete the vectors no index of the path's repository references, "
            "and stop.",
            action="store_true",
        ),
    )
    login_days: int = field(
        default=30,
        metadata=_opt(
            SERVER,
            "Days a login token from ish remote login lasts.",
            type=int,
            metavar="DAYS",
        ),
    )
    ldap_url: str = field(
        default="",
        metadata=_opt(
            SERVER,
            "URL of the LDAP directory that ldap in auth signs users in to.",
            metavar="URL",
        ),
    )
    ldap_user_dn: str = field(
        default="",
        metadata=_opt(
            SERVER,
            "Name a user binds as, with {user} for the user name.",
            metavar="DN",
        ),
    )
    ldap_bind_dn: str = field(
        default="",
        metadata=_opt(
            SERVER,
            "Service account that searches the directory for each user. "
            "Its password is in ISH_LDAP_BIND_PASSWORD.",
            metavar="DN",
        ),
    )
    ldap_base: str = field(
        default="",
        metadata=_opt(
            SERVER,
            "Part of the directory the service account searches for users.",
            metavar="DN",
        ),
    )
    ldap_user_filter: str = field(
        default="",
        metadata=_opt(
            SERVER,
            "Filter that finds a user, with {user}. Empty means (uid={user}).",
            metavar="FILTER",
        ),
    )
    ldap_group: str = field(
        default="",
        metadata=_opt(
            SERVER,
            "Give a login token only to a member of this LDAP group.",
            metavar="DN",
        ),
    )
    ldap_starttls: bool = field(
        default=False,
        metadata=_opt(
            SERVER,
            "Start TLS on a plain ldap:// URL before the password goes out.",
            action="store_true",
        ),
    )
    auth: tuple[str, ...] = field(
        default=("token",),
        metadata=_opt(
            SERVER,
            "Ways the server admits a request. token admits the token in "
            "ISH_MCP_TOKEN.",
            nargs="+",
            metavar="METHOD",
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
    """Read the options of one config file, and warn for an unknown key."""
    return _accept(str(path), _file_options(path))


def _file_options(path: Path) -> Mapping[str, Any]:
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
    return _options_in(path, raw)


# Options that a file in a tree may not set. A tree that names the
# server of `ish remote` chooses where the token of the user goes. A tree
# that sets how a server signs users in chooses the directory that gets
# each password, and how long a login lasts.
NOT_FROM_A_TREE = (
    "remote",
    "auth",
    "login_days",
    "ldap_url",
    "ldap_user_dn",
    "ldap_bind_dn",
    "ldap_base",
    "ldap_user_filter",
    "ldap_group",
    "ldap_starttls",
)


def _from_a_tree(path: Path) -> dict[str, Any]:
    """Read the project config file *path*, without what a tree may not set.

    Drop each such option with no word. Only a command that reads a
    server names the file, through ``tree_files_naming_a_server``, so a
    local command prints what it printed before.
    """
    options = _read_toml(path)
    for name in NOT_FROM_A_TREE:
        options.pop(name, None)
    return options


def tree_files_naming_a_server(
    start: Path,
    *,
    config: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    """Return each project config file at or above *start* that sets ``remote``.

    The loader ignores ``remote`` in each one. Return nothing when
    *config* or ``ISH_CONFIG`` names a file, because the loader then
    reads no project file.
    """
    return [
        path
        for path, names in tree_files_setting(start, config=config, environ=environ)
        if "remote" in names
    ]


def tree_files_setting(
    start: Path,
    *,
    config: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[tuple[Path, list[str]]]:
    """Return each project config file at or above *start* that sets an
    option of ``NOT_FROM_A_TREE``, with the names of those it sets.

    The loader ignores each one. Return nothing when *config* or
    ``ISH_CONFIG`` names a file, because the loader then reads no project
    file.
    """
    environ = environ if environ is not None else os.environ
    if config or environ.get(CONFIG_ENV_VAR):
        return []
    found = []
    for path in project_configs(start):
        names = [name for name in NOT_FROM_A_TREE if name in _file_options(path)]
        if names:
            found.append((path, names))
    return found


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

    Pass over ``ISH_CONFIG``, ``ISH_MCP_TOKEN``, and the two variables of
    the LDAP password. One names a file to read, and the others hold a
    secret or name its file. None is an option, and reporting one as
    unknown would be wrong.
    """
    skipped = (
        CONFIG_ENV_VAR,
        MCP_TOKEN_ENV_VAR,
        LDAP_PASSWORD_ENV_VAR,
        LDAP_PASSWORD_FILE_ENV_VAR,
    )
    raw = {
        key.removeprefix(ENV_PREFIX).lower(): value
        for key, value in environ.items()
        if key.startswith(ENV_PREFIX) and key not in skipped
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
            settings = replace(settings, **_from_a_tree(project))

    settings = replace(settings, **_from_env(environ))

    if supplied:
        settings = replace(settings, **_accept("the command line", supplied))

    return settings
