---
paths:
  - "src/ish/adapters/parser/**"
---

# Parsers

## Adding a language

1. Write the parser in `<language>.py` in this package. A language that
   needs a second file is a subpackage, `<language>/`. The layout follows
   `architecture.md`. The parser declares `language`, `suffixes` with the leading dot, and
   `parse(path, source) -> Sequence[Chunk]`. That is the whole `Parser` port.
2. Add one `PARSERS` entry in `__init__.py`. The entry carries the aliases a
   reader may type and the category the language holds: `code`, `doc`, or
   `config`.

Nothing else changes. Not `Scan`, not the port, not `bootstrap`, not an
interface. `tests/unit/adapters/parser/test_registry.py` checks the entry.
The `add-language` skill walks through it.

## Rules

- A change to a parser carries a `/chunk-diff` in its commit body: `identical`
  for a move, or the chunks the change names. A changed chunk raises
  `CHUNKING_VERSION`.

- Stamp `language` onto every chunk the parser emits.
- Two parsers claiming one suffix is a hard error. Resolve it with the
  `languages` option, not by editing the other parser.
- Do not cap chunk size inside a parser. `SizeLimited` wraps every parser in
  `build_parsers()`, so a plugin gets the cap without asking. `CountLimited`
  wraps outside it and reads a file of more than `max_chunks` chunks as one.
- `MAX_CHUNK_CHARS` is a constant, not a setting. It describes what the
  embedding model reads at the default window. `chars_for()` scales it to
  the `context_tokens` setting, and `build_parsers()` hands the result to
  the wrappers. A parser that divides by size itself exposes `limit`, which
  `build_parsers()` sets the same way.
- Import a grammar or a library inside the method that needs it. Listing the
  registry must cost nothing.
- Raise `ParseError` when nothing parses. A parser that recognizes part of a
  broken file returns what it recognized.
- A file that yields no chunks is not an error. Return an empty sequence.
- Report a value no rule can divide once per file, with a count and the
  largest by name. A generated document holds thousands of them.
- Only a language module carries a plain name in this package. Machinery is
  prefixed with an underscore: `_limits.py`, `_plugins.py`, `_sections.py`.

## What each parser owns

- `_structured.py` reads YAML and JSON for `yaml.py` and `json.py`. Split a
  document at the first list of things it holds. Do not split an entry's own
  fields.
- `_sections.py` holds the section walk that `markdown.py` and `asciidoc/`
  share. A section runs to the next heading. The symbol is the heading path. Skip
  fenced blocks. Each language passes its own fence: in Markdown only
  backticks and tildes, because a line of hyphens or equal signs is a
  thematic break or a heading underline there. A file with no heading is one chunk named after the file
  when it holds a line of prose, and nothing when it holds only machinery.
- `asciidoc/` overrides three hooks of the walk: `_expand`, `_blocks`, and
  `_pieces`. It resolves includes through `asciidoc/_includes.py`: attributes,
  attribute paths, and tagged regions, read relative to the including file.
  Sections still divide at the headings of the file, so line numbers stay
  true, and the parser divides a long section itself, between lines of the
  file, under the `limit` that `build_parsers()` sets. `asciidoc/_tables.py`
  finds the tables in the expanded text. A table that fits is
  kept whole. A longer one divides between rows, and each piece repeats its
  head and closes it. A figure reads as `Figure: alt text`. An include
  that brings in more than `INLINE_CHARS` is a `table` chunk of its own,
  its table with it, and the section keeps its prose, with the directive
  left in place, as another.
- `ask` reads a chunk through `build_reader()`, which parses the file again,
  so a model reads the text the index embedded, includes resolved.
- `_tree_sitter.py` walks a Tree-sitter grammar. `cpp.py` configures it for C
  and C++ as one parser, `cpp`, which owns `.h`. A `.c` file the C++ grammar
  leaves an error in is read again with the C grammar, which reads a K&R
  definition. The second reading wins only with fewer errors and no chunk
  lost, so a clean file is parsed once. A
  type is a definition only when it has a body. A declaration is a chunk only
  when it declares a function, and is dropped when the same file defines it.
- `_plugins.py` loads a parser from `~/.config/ish/parsers/`. Only that
  directory, because a parser is code and code in a checkout was not chosen.
