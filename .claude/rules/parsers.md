---
paths:
  - "src/ish/adapters/parser/**"
---

# Parsers

## Adding a language

1. Write the parser class in a new module in this package, beside the
   others. It declares `language`, `suffixes` with the leading dot, and
   `parse(path, source) -> Sequence[Chunk]`. That is the whole `Parser` port.
2. Add one `PARSERS` entry in `__init__.py`. The entry carries the aliases a
   reader may type and the category the language holds: `code`, `doc`, or
   `config`.

Nothing else changes. Not `Scan`, not the port, not `bootstrap`, not an
interface. `tests/unit/adapters/parser/test_registry.py` checks the entry.
The `add-language` skill walks through it.

## Rules

- Stamp `language` onto every chunk the parser emits.
- Two parsers claiming one suffix is a hard error. Resolve it with the
  `languages` option, not by editing the other parser.
- Do not cap chunk size inside a parser. `SizeLimited` wraps every parser in
  `build_parsers()`, so a plugin gets the cap without asking. `CountLimited`
  wraps outside it and reads a file of more than `max_chunks` chunks as one.
- `MAX_CHUNK_CHARS` is a constant, not a setting. It describes what the
  embedding model can read.
- Import a grammar or a library inside the method that needs it. Listing the
  registry must cost nothing.
- Raise `ParseError` when nothing parses. A parser that recognizes part of a
  broken file returns what it recognized.
- A file that yields no chunks is not an error. Return an empty sequence.
- Report a value no rule can divide once per file, with a count and the
  largest by name. A generated document holds thousands of them.
- Only a language module carries a plain name in this package. Machinery is
  prefixed with an underscore: `_limits.py`, `_plugins.py`.

## What each parser owns

- `structured.py` reads YAML and JSON. Split a document at the first list of
  things it holds. Do not split an entry's own fields.
- `markup.py` reads Markdown and AsciiDoc as one parser built twice. A
  section runs to the next heading. The symbol is the heading path. Skip
  fenced blocks.
- `tree_sitter.py` reads C and C++ as one parser, `cpp`, which owns `.h`. A
  type is a definition only when it has a body. A declaration is a chunk only
  when it declares a function, and is dropped when the same file defines it.
- `_plugins.py` loads a parser from `~/.config/ish/parsers/`. Only that
  directory, because a parser is code and code in a checkout was not chosen.
