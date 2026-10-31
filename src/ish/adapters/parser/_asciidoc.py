"""Resolve the includes of an AsciiDoc file, as the text a reader would see.

A specification assembles its tables from other files. A section lists
its registers as ``include::{_REG_TIMER_LOAD}[{REG_TIMER_LOAD}]``,
where one attribute names the file and another the tagged region of it,
and both are defined in attribute files that the document includes first.
Left as written, the section holds the directives and none of the values.

Expand each line of a file into the text it stands for: an include
becomes the lines it selects, expanded in turn, and every other line
stays as it is. Keep one result per line of the file, so a section still
runs from its heading to the next and its line numbers stay true.

Asciidoctor does more than this. Here an attribute is set when it is
defined and read when an include names it, a tag selects the lines
between ``tag::name[]`` and ``end::name[]``, and a path is read relative
to the file that holds the include. An included file shows its text but
not its attribute definitions or comments, so a file of 64,000
definitions adds attributes and no lines. Conditional directives stay as
text. A file that cannot be read leaves its include as written.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Includes nest: a document includes an attribute file that includes
# another. Stop at this depth, which also ends a file that includes itself.
MAX_DEPTH = 8

_ATTRIBUTE = re.compile(r"^:(!?)([\w][\w-]*)(!?):(?:\s+(.*?))?\s*$")
_INCLUDE = re.compile(r"^include::([^\[]+)\[([^\]]*)\]\s*$")
_REFERENCE = re.compile(r"\{([\w][\w-]*)\}")
_TAG = re.compile(r"(?:tag|end)::([^\[]+)\[\]")


@dataclass
class _Context:
    """Hold what one expansion has learned so far."""

    attributes: dict[str, str] = field(default_factory=dict)
    reading: list[Path] = field(default_factory=list)
    missing: set[Path] = field(default_factory=set)


class Expander:
    """Expand the includes of AsciiDoc files, and remember the files it read.

    One expander serves a whole index run. An attribute file of 64,000
    lines is included by every specification of a tree, so read each file
    once and keep its lines while its modification time stands.
    """

    def __init__(self) -> None:
        self._files: dict[Path, tuple[float, list[str]]] = {}
        # A file of definitions alone, and the attributes it reads that it
        # does not define itself. None for a file that shows text.
        self._pure: dict[Path, frozenset[str] | None] = {}
        # What including such a file sets, for the values it reads.
        self._effects: dict[tuple, dict[str, str | None]] = {}

    def expand(self, path: Path, lines: list[str]) -> list[str]:
        """Return the text each line of *path* stands for, one entry a line."""
        context = _Context(reading=[path.resolve()])
        expanded = [self._line(path, line, context, depth=0) for line in lines]
        if context.missing:
            log.debug(
                "%s: %d included files could not be read",
                path,
                len(context.missing),
            )
        return expanded

    def _line(self, path: Path, line: str, context: _Context, depth: int) -> str:
        """Return the text of one line: its include expanded, or the line itself."""
        definition = _ATTRIBUTE.match(line)
        if definition:
            self._define(definition, context)
            # An included file of definitions sets attributes and shows
            # nothing. The file being expanded keeps its own lines.
            return line if depth == 0 else ""
        if depth > 0 and line.lstrip().startswith("//"):
            return ""
        include = _INCLUDE.match(line.rstrip("\n"))
        if include is None:
            return line
        target = self._substitute(include.group(1).strip(), context)
        options = self._substitute(include.group(2), context)
        if "{" in target:
            return line
        file = (path.parent / target).resolve()
        if depth >= MAX_DEPTH or file in context.reading:
            return line
        lines = self._read(file, context)
        if lines is None:
            return line
        tags = _tags(options)
        if not tags and self._apply_cached(file, lines, context):
            return ""
        context.reading.append(file)
        try:
            chosen = _select(lines, tags)
            text = "".join(self._line(file, raw, context, depth + 1) for raw in chosen)
        finally:
            context.reading.pop()
        if text and not text.endswith("\n"):
            text += "\n"
        return text

    def _apply_cached(self, file: Path, lines: list[str], context: _Context) -> bool:
        """Set what a file of definitions sets, from the cache when it can.

        A specification includes an attribute file of 64,000 lines, and
        every specification of a tree includes the same one. Its effect
        depends only on the attributes its values read, so key it by them.
        Return False for a file that shows text, which is expanded instead.
        """
        reads = self._pure.get(file, _UNSEEN)
        if reads is _UNSEEN:
            reads = _definition_reads(lines)
            self._pure[file] = reads
        if reads is None:
            return False
        key = (
            file,
            self._files[file][0],
            tuple((n, context.attributes.get(n)) for n in sorted(reads)),
        )
        effect = self._effects.get(key)
        if effect is None:
            before = dict(context.attributes)
            for raw in lines:
                definition = _ATTRIBUTE.match(raw)
                if definition:
                    self._define(definition, context)
            effect = {
                name: context.attributes.get(name)
                for name in set(before) | set(context.attributes)
                if before.get(name) != context.attributes.get(name)
            }
            self._effects[key] = effect
            return True
        for name, value in effect.items():
            if value is None:
                context.attributes.pop(name, None)
            else:
                context.attributes[name] = value
        return True

    def _define(self, match: re.Match[str], context: _Context) -> None:
        """Set an attribute, or unset it for ``:name!:`` and ``:!name:``."""
        unset, name, unset_after, value = match.groups()
        if unset or unset_after:
            context.attributes.pop(name, None)
            return
        context.attributes[name] = self._substitute(value or "", context)

    @staticmethod
    def _substitute(text: str, context: _Context) -> str:
        """Replace each ``{name}`` the attributes define, and keep the rest."""
        return _REFERENCE.sub(
            lambda m: context.attributes.get(m.group(1), m.group(0)), text
        )

    def _read(self, file: Path, context: _Context) -> list[str] | None:
        """Return the lines of *file*, from the cache while it has not changed."""
        try:
            stamp = file.stat().st_mtime
        except OSError:
            context.missing.add(file)
            return None
        cached = self._files.get(file)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            lines = file.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            context.missing.add(file)
            return None
        self._files[file] = (stamp, lines)
        return lines


_UNSEEN = frozenset({"\0unseen"})


def _definition_reads(lines: list[str]) -> frozenset[str] | None:
    """Return the attributes a file of definitions reads from outside.

    Return None for a file with any line that is not a definition, a
    comment, or blank, because such a file shows text where it is
    included.
    """
    defined: set[str] = set()
    reads: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        definition = _ATTRIBUTE.match(line)
        if definition is None:
            return None
        name, value = definition.group(2), definition.group(4) or ""
        reads.update(r for r in _REFERENCE.findall(value) if r not in defined)
        defined.add(name)
    return frozenset(reads)


def _tags(options: str) -> list[str]:
    """Return the tags an include selects: ``tag=a`` or ``tags=a;b``."""
    for option in options.split(","):
        key, _, value = option.strip().partition("=")
        if key.strip() in ("tag", "tags"):
            names = re.split(r"[;,]", value.strip().strip('"'))
            return [n.strip() for n in names if n.strip() and not n.startswith("!")]
    return []


def _select(lines: list[str], tags: list[str]) -> list[str]:
    """Keep the lines inside the named tags, or every line but tag markers."""
    if not tags:
        return [line for line in lines if not _TAG.search(line)]
    chosen: list[str] = []
    inside: set[str] = set()
    for line in lines:
        marker = _TAG.search(line)
        if marker:
            name = marker.group(1)
            if marker.group(0).startswith("tag::"):
                inside.add(name)
            else:
                inside.discard(name)
            continue
        if inside & set(tags):
            chosen.append(line)
    return chosen
