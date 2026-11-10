"""Test that every file the docs name is a file in this repository.

The map in ``.claude/CLAUDE.md``, the rules, the skills, the docs, and the
benchmark queries name modules by path. A rename that misses one of them
leaves a reader, or the model, looking for a file that is gone. These
guards fail on the first name that no file answers.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Directories that hold no source of this repository.
SKIP = {
    ".git",
    ".cache",
    "site",
    "dist",
    "build",
    "node_modules",
    "__pycache__",
    "worktrees",
    "graphify-out",
}

# Names the docs use for files outside this repository, with the reason.
OUTSIDE = {
    ".claude/worktrees/": "created at run time by a worktree",
    ".ish/config.toml": "the configuration of a tree a user indexes",
    "ish.toml": "the flat form of that configuration",
    "lua/utils/ish.lua": "where a user copies the Neovim picker",
    "lua/utils/ish_link.lua": "where a user copies the Neovim link opener",
    "lua/utils/ish_ask.lua": "where a user copies the Neovim ask command",
    "spec.md": "written by /spec-interview while a design is under way",
    "tasks.md": "written by /spec-interview with spec.md",
}

# A name in backticks that reads as a path: a file with a known suffix, or
# a path under a top-level directory of this repository.
_PATH = re.compile(
    r"^[\w./-]+\.(py|md|toml|lua|ts|ya?ml|json|sh)$"
    r"|^(src|tests|scripts|docs|contrib|\.claude|\.github)/[\w./-]*$"
)
_TOKEN = re.compile(r"`([^`\s]+)`")


def _tracked() -> list[str]:
    """Return the path of every file in the repository, relative to its root."""
    return [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and not SKIP & set(path.relative_to(ROOT).parts)
        and not any(part.startswith(".venv") for part in path.parts)
        and not any(part.endswith(".egg-info") for part in path.parts)
    ]


def _documents() -> list[Path]:
    """Return the documents that name files: the map, rules, skills, and docs."""
    return [
        ROOT / ".claude" / "CLAUDE.md",
        ROOT / "README.md",
        *sorted((ROOT / ".claude" / "rules").glob("*.md")),
        *sorted((ROOT / ".claude" / "skills").glob("**/*.md")),
        *sorted((ROOT / "docs").glob("**/*.md")),
    ]


def _named(document: Path) -> set[str]:
    """Return each name in backticks in *document* that reads as a path."""
    names = set()
    for token in _TOKEN.findall(document.read_text()):
        name = token.split(":")[0].split("#")[0]
        if _PATH.match(name) and not any(c in name for c in "<>*$~{}"):
            names.add(name)
    return names


def _exists(name: str, files: list[str]) -> bool:
    """Return True when *name* is a file, a directory, or a path suffix."""
    name = name.rstrip("/")
    return any(
        f == name or f.endswith("/" + name) or f.startswith(name + "/") for f in files
    )


FILES = _tracked()


@pytest.mark.parametrize(
    "document", _documents(), ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_every_named_file_exists(document: Path) -> None:
    missing = sorted(
        name
        for name in _named(document)
        if name not in OUTSIDE and not _exists(name, FILES)
    )
    assert not missing, missing


def test_every_benchmark_query_names_a_file() -> None:
    queries = ROOT / ".claude" / "skills" / "benchmark" / "queries.toml"
    entries = tomllib.loads(queries.read_text())["query"]
    missing = [
        entry["path"]
        for entry in entries
        if "path" in entry and not any(f.endswith(entry["path"]) for f in FILES)
    ]
    assert not missing, missing


def test_every_outside_name_is_still_named() -> None:
    """A name the docs dropped leaves the list, so the list stays true."""
    named = set().union(*(_named(document) for document in _documents()))
    assert set(OUTSIDE) <= named, sorted(set(OUTSIDE) - named)


def test_a_stale_name_is_caught() -> None:
    assert not _exists("src/ish/adapters/parser/markup.py", FILES)
    assert _exists("_sections.py", FILES)
