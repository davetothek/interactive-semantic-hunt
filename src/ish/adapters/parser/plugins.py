"""Load parsers the user wrote.

The ``Parser`` protocol is the whole contract, so a parser needs no
registration beyond being found: a module that exposes ``parser()``
returning something with a language, its suffixes, and a ``parse``
method is a parser.

Only the user's own configuration directory is read. A parser is code,
and code that arrives with a checked-out repository is code nobody
chose to run.
"""

import importlib.util
import logging
import os
from collections.abc import Callable
from pathlib import Path

from ish.application.ports.parser import Parser

log = logging.getLogger(__name__)

FACTORY_NAME = "parser"


def plugin_dir() -> Path:
    """Return the directory holding user-written parsers."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ish" / "parsers"


def load_parsers(directory: Path | None = None) -> dict[str, Callable[[], Parser]]:
    """Return a factory for every parser found, keyed by its language.

    Report and skip a module that cannot be loaded or does not satisfy
    the protocol, so one broken parser cannot stop the tool.
    """
    directory = directory or plugin_dir()
    if not directory.is_dir():
        return {}

    found: dict[str, Callable[[], Parser]] = {}
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        parser = _load_one(path)
        if parser is None:
            continue
        if parser.language in found:
            log.warning(
                "Two parsers in %s claim the language %r. Keeping the first.",
                directory,
                parser.language,
            )
            continue
        found[parser.language] = _factory(path)
        log.debug(
            "Loaded parser %r from %s for %s",
            parser.language,
            path.name,
            sorted(parser.suffixes),
        )
    return found


def _factory(path: Path) -> Callable[[], Parser]:
    """Return a callable that builds the parser in *path* on demand."""

    def build() -> Parser:
        parser = _load_one(path)
        if parser is None:
            raise ValueError(f"The parser in {path} could no longer be loaded.")
        return parser

    return build


def _load_one(path: Path) -> Parser | None:
    """Import one module and take the parser it offers."""
    try:
        spec = importlib.util.spec_from_file_location(f"ish_parser_{path.stem}", path)
        if spec is None or spec.loader is None:
            log.warning("Cannot load a parser from %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        log.warning("The parser in %s failed to load: %s", path, exc)
        return None

    factory = getattr(module, FACTORY_NAME, None)
    if not callable(factory):
        log.warning(
            "%s defines no %s() function, so it offers no parser.",
            path,
            FACTORY_NAME,
        )
        return None

    try:
        parser = factory()
    except Exception as exc:
        log.warning("The parser in %s could not be built: %s", path, exc)
        return None

    return _validated(parser, path)


def _validated(parser: object, path: Path) -> Parser | None:
    """Return the parser when it satisfies the port, or report why not."""
    if not isinstance(parser, Parser):
        log.warning(
            "%s returned something that is not a parser. It needs a language, "
            "suffixes, and a parse method.",
            path,
        )
        return None

    if not getattr(parser, "language", ""):
        log.warning("%s returned a parser with no language.", path)
        return None

    suffixes = getattr(parser, "suffixes", frozenset())
    if not suffixes:
        log.warning("%s returned a parser that claims no suffix.", path)
        return None
    if not all(str(s).startswith(".") for s in suffixes):
        log.warning(
            "%s claims a suffix without a leading dot: %s",
            path,
            sorted(suffixes),
        )
        return None

    return parser
