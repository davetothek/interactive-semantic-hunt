---
name: add-language
description: >-
  Add a language to ish: one parser module under adapters/parser and one
  PARSERS entry, with the tests the registry expects. Use it when the user
  asks to support a new file type, language, or format, or to add a parser.
argument-hint: "[language-name]"
---

# Add the language `$ARGUMENTS`

A language is one module and one registry line. Nothing else changes: not
`Scan`, not the `Parser` port, not `bootstrap`, not an interface. If you find
yourself editing any of those, stop and ask.

When the user asks for a dry run, list the files you would create and
change, with the suffixes and aliases you would claim, and stop.

## 1. Claim the suffixes

```
grep -rn "suffixes" src/ish/adapters/parser/*.py
```

Two parsers claiming one suffix is a hard error. Pick suffixes nobody holds,
each with the leading dot.

## 2. Write the module

`src/ish/adapters/parser/$ARGUMENTS.py`:

```python
"""Parse <format> into chunks. <One sentence on what a chunk is here.>"""

from collections.abc import Sequence
from pathlib import Path

from ish.application.ports.parser import ParseError
from ish.domain.chunk import Chunk


class <Name>Parser:
    """Emit one chunk per <unit>."""

    language = "$ARGUMENTS"
    suffixes = frozenset({".<ext>"})

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        # Import a grammar or a library here, so listing the registry
        # costs nothing.
        ...
```

Rules the module must keep:

- Stamp `language` on every `Chunk`. `start_line` and `end_line` count from
  one and are inclusive. `text` is the exact source of that range.
- Raise `ParseError` when nothing parses. Return what parsed when part of a
  file is broken. Return an empty sequence for a file that holds no unit.
- Do not cap chunk size. `SizeLimited` wraps every parser.
- `symbol` is what a reader would search for: a qualified name, or a heading
  path joined with ` > `. `kind` names the unit: `function`, `class`,
  `section`, `document`.

Read `.claude/rules/parsers.md` when it loads. `markup.py` is the smallest
existing parser to copy from. `structured.py` shows splitting a document.

## 3. Register

In `src/ish/adapters/parser/__init__.py`, one line in `PARSERS`:

```python
"$ARGUMENTS": Language(<Name>Parser, frozenset({"<alias>", ...}), <CODE|DOC|CONFIG>),
```

Aliases are the other names a reader may type after `lang:`. The category
says what the language holds and decides how `type:` sorts it.

## 4. Test

`tests/unit/adapters/parser/test_$ARGUMENTS_parser.py`, against known source
strings: the smallest valid file, several units, nesting if the format has
it, the line numbers of each chunk, the exact `text`, a file with no unit, a
broken file. `test_registry.py` checks the registry entry on its own.

## 5. Prove it end to end

```
uv run python -c "
from pathlib import Path
from ish.adapters.parser import PARSERS
p = PARSERS['$ARGUMENTS'].build()
for c in p.parse(Path('sample.<ext>'), Path('sample.<ext>').read_text()):
    print(f'{c.start_line}-{c.end_line}  {c.kind}  {c.symbol}')
"
```

Then confirm the vocabulary followed: `uv run ish --help | grep -A2 lang`
lists the new name and its aliases.

## 6. Finish

Add a line under `### Added` in `CHANGELOG.md`. Run `/check`. Commit with
the language in the subject. Do not open a pull request.
