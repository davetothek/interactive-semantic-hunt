# ish — Interactive Semantic Hunt

Semantic search for code, inspired by `fzf`. Point `ish` at a directory. It parses
each source file into named chunks, embeds them with a local model, and ranks them
against your query. Everything runs on your machine.

Languages: Python, C and C++, Markdown, AsciiDoc, YAML, and JSON. Documentation
and configuration are indexed beside the code they describe, so one query
searches all of it.

## Install

```sh
pip install interactive-semantic-hunt
```

Nothing in that install compiles: the default backend reaches Ollama over
HTTP with the standard library. A backend that runs the model in this
process is an extra — `[llama]` for llama.cpp, `[st]` for
sentence-transformers.

To work on ish itself, clone it and run `uv sync`.

The default embedding backend is Ollama, which keeps the model resident so no
run pays a model load. Start it once and pull the embedding model:

```sh
ollama serve
ollama pull nomic-embed-text
```

Set `OLLAMA_HOST` to reach a daemon elsewhere.

Two other backends need no daemon:

- `--embedder llama.cpp` downloads a GGUF model and loads it per run. Slower per
  query, faster for a first index of a large tree. Needs the `[llama]` extra.
- `--embedder st` uses sentence-transformers. Needs the `[st]` extra.

## Use

List every chunk under a path:

```sh
ish src/
```

```
src/ish/domain/chunk.py:7-28  class  Chunk
```

Search for a query:

```sh
ish "parse a python file" src/
```

```
[0.71] src/ish/adapters/parser/python.py:14-31  method  PythonParser.parse
```

Run the interactive picker and open the selection in your editor. Type to
search, `up`/`down` or `ctrl+p`/`ctrl+n` to move, `enter` to choose, `ctrl+t`
to change the theme, `escape` to quit. Narrow without leaving the query
line:

```text
state machine transitions              every language
lang:cpp state machine transitions     the implementation
lang:yaml under:/10.System/ state      the tests that cover it
type:doc how do I configure this       the prose, not the code
type:test,doc retry backoff            the tests and what they document
```

Press Tab to finish a filter word. `ty` becomes `type:`, `lang:cp` becomes
`lang:cpp`, and `under:/s` becomes `under:/src/`; a word with several answers
grows as far as they agree and names the rest. `ish-complete` does the work, so
any picker can call it.

`lang:`, `under:`, and `type:` work in the query line of every interface —
the command line, the picker, Neovim, and MCP. The words are taken out
before the query is embedded, so the model sees the question rather than
how it was narrowed.

A language may be named however it comes to mind. `c`, `c++`, `cxx`, `h`, and
`hpp` all mean `cpp`, because one parser reads them all; `adoc` means
`asciidoc`, `md` means `markdown`, `py` means `python`, and `yml` means `yaml`.

`type:` sorts every chunk into exactly one kind. A path decides before a
language does, so a YAML fixture counts as a test rather than as config:

| kind | what it holds |
|---|---|
| `code` | Python, C, C++, and anything a plugin parser adds |
| `doc` | Markdown and AsciiDoc |
| `test` | anything under `tests/`, `spec/`, `fixtures/`, plus `test_*` and `conftest.py` |
| `config` | YAML, JSON, and TOML outside a test path |

A repository that names its trees its own way can say so, in
`.ish/config.toml`:

```toml
type_patterns = [
  "test:/[0-9.]*(Tests|Verification)/",
  "doc:/[0-9.]*Specification/",
]
```

The first match wins; anything unmatched keeps the reading above.

A config beside a subtree adds to the one above it, so a tree settles only
what it names and inherits the rest. Searching inside an already-indexed tree
reads that tree's index and narrows the answers to the path, rather than
starting a second index of the same files.

```sh
nvim $(ish -i src/)
```

### Options

| Flag | Purpose |
|---|---|
| `-i`, `--interactive` | Run the TUI picker |
| `-c`, `--config PATH` | Read the options from this file, not from the search upward |
| `--embedder {llama.cpp,ollama,st}` | Select the embedding backend (default: ollama) |
| `-v`, `-vv` | Increase log detail |
| `--color {auto,always,never}` | Control log color |
| `--limit N` | Maximum search results |
| `--ignore DIR ...` | Directory names to skip (default `.git .venv venv __pycache__`) |
| `--include REGEX ...` | Index only paths matching these patterns |
| `--exclude REGEX ...` | Never index paths matching these patterns |
| `--git`, `--no-git` | Skip files git ignores (default: on) |
| `--lang LANG ...` | Show results only from these languages |
| `--under REGEX` | Show results only from matching paths |
| `--type TYPE ...` | Show results only of these kinds: `code`, `doc`, `test`, `config` |
| `--type-patterns TYPE:REGEX ...` | Say what a path holds, overriding the built-in reading |
| `--model NAME` | Override the backend model |
| `--refresh` | Bring every stored index at or below the path up to date first |
| `--reindex` | Discard the stored index and build it again |
| `--no-cache` | Index in memory only, leaving nothing on disk |
| `--tui-theme NAME` | Theme for the picker (default: the Textual default) |

Logs go to stderr, so you can pipe stdout safely. A file that cannot be read
or parsed is counted in one line; `-v` names them.

### Theme the picker

`ctrl+t` steps to the next theme while the picker runs and names the one it
lands on. The choice lasts for that run. Say where the next run starts with
`tui_theme`, in the config file or as `--tui-theme NAME`:

```toml
tui_theme = "solarized-light"
```

Textual holds a theme for a light terminal as well as for a dark one, and
the preview follows: it reads the source in `gruvbox-light` under a light
theme and in `gruvbox-dark` under a dark one. A name no theme answers to is
reported in the picker, which then keeps the default.

`NO_COLOR` still wins over all of it. Set it and the picker draws in
monochrome, the preview included, whatever theme is chosen.

## Use from Neovim

`contrib/nvim/ish.lua` is an fzf-lua picker. Copy it to `lua/utils/ish.lua` and
bind it:

```lua
map('n', '<leader>fi', function() require('utils.ish').search() end,
    { desc = 'Semantic search (ish)' })
```

It reads `--format grep`, so the built-in previewer opens each result at its
line, and prints the rank in the leftmost column. `search_lang({'cpp'})`,
`search_type({'doc'})`, and `search_here()` narrow it, as does a `lang:`,
`type:`, or `under:` word typed into the query.

While the index refreshes, `require('utils.ish').statusline()` renders a bar
for a statusline — `ish ███░░░░░ 38%` — and an empty string when idle. It reads `vim.g.ish_index_status`, which the picker keeps up to date, and
shows `ish ✓` briefly when a refresh finishes. Nothing is reported through
`vim.notify`: with `cmdheight = 0` there is no command line to put a message
in, so nvim draws one over the last screen row — the statusline itself.

The picker never blocks the editor: results are written as they arrive, so
typing stays smooth however long a search takes.

`contrib/nvim/ish_server.lua` keeps one `ish-mcp` process per session. It
starts on the first search and is reused after that, which cuts a keystroke
from about 500 ms to about 150 ms. Copy it beside the picker.

## Use from Python

```python
from ish.interfaces.python.api import Ish

with Ish("src/") as ish:
    for chunk, score in ish.search("type:doc how to configure", limit=5):
        print(score, chunk.path, chunk.symbol)
    print(ish.status())
```

`Ish` holds the index open, so a second query costs a search rather than a
process start. It offers `search()`, `chunks()`, `index()`, `refresh_all()`,
and `status()`, and reads `lang:`, `type:`, and `under:` out of the query
exactly as the other interfaces do.

## Use from an agent

`ish-mcp` serves the same search over the Model Context Protocol, so an agent
can query the index directly. Add it to a project with `.mcp.json`:

```json
{
  "mcpServers": {
    "ish": { "command": "uv", "args": ["run", "ish-mcp"] }
  }
}
```

It offers `search_code`, `list_chunks`, `index_status`, and `refresh_index`. The server stays
resident, so a query costs about 58 ms rather than a process start.

A call may narrow one search with `lang`, `under`, `type`, and `limit`, or
write the same filters into the query text. It cannot change
what is indexed — those settings come from the config file only, so no single
call can shrink an index that another call depends on.

## Index

The index persists in SQLite under `$XDG_DATA_HOME/ish/`, one file per scanned
tree. **A search of a parent reads the indexes below it and refreshes none** —
choosing one of them to write to would be wrong — so it warns and offers
`--refresh`, which visits each tree in turn. A repeated query reuses it, so only changed files are parsed and only new
text is embedded. A renamed file re-embeds nothing.

Each index records the tree it was built from, so searching a directory also
searches every index below it. Index the parts of a large project separately and
search the whole from its root:

```sh
ish "warm" project/docs        # index one part
ish "warm" project/firmware    # and another
ish "how is exposure set" project    # searches both
```

Searching a parent never rewrites an index below it. Pass `--no-federate` to use
only the index of the exact path.

The index records where each chunk is — its path, line range, kind, and name —
together with the embedding vector. It does not store the source, so it is not a
second readable copy of your code. Previews are read from the file, which also
means they always show the current content.

## Configure

Every command-line option is also a key in the config file, under the same
name. The one exception is `--config` itself: a file cannot name where to
find itself. Put project settings in `.ish/config.toml` at the root of your
repository:

```toml
embedder = "ollama"
model = "mxbai-embed-large"
limit = 10
ignore = [".git", ".venv", "build", "node_modules"]

# Regular expressions, searched against the path.
exclude = ["/vendor/", "_pb2\\.py$", "(_test|_spec)\\.py$"]
```

`include` and `exclude` take regular expressions rather than globs, so `/vendor/`
matches at any depth and alternation works. `exclude` wins over `include`.

`--git` is on by default, so anything a `.gitignore` covers stays out of the
index. Pass `--no-git` to index it anyway.

### Keep generated code out

Generated code is the one thing worth excluding by hand. It is large, it is
repetitive, and nobody searches it by meaning. On one firmware tree, generated
headers were **99% of the oversized C and C++ text**: 10.3 MB of 10.4 MB, and
the largest single definition held 2,949,177 characters. A 26 MB generated JSON
register map produced 32,768 chunks and took 76 seconds to parse.

Embedding costs about one chunk per second, so that is hours of work for text
no query wants.

Name the patterns in `exclude`:

```toml
exclude = [
    "/generated/",           # a directory that holds nothing written by hand
    "_pb2\\.py$",             # protobuf
    "\\.g\\.(c|h|cpp)$",       # a generator's own suffix
    "/build/",               # anything a build wrote
    "register_map.*\\.json$", # a generated register map
]
```

Two things to know:

- `exclude` is index scope, so it decides what is *in* the index. Narrowing it
  later does not remove what a wider run already stored; run `--reindex` for
  that.
- A pattern is a regular expression searched against the whole path, so
  `/generated/` matches at any depth and needs no wildcards.

Check a pattern before you pay to index it. An empty query lists what the
filter allows, and `--no-cache` keeps the trial out of the stored index:

```sh
ish "" . --no-cache | wc -l                          # everything today
ish "" . --no-cache --exclude '/generated/' | wc -l  # what the pattern leaves
```

If most of a tree is generated, it is usually less work to exclude the
directory than to name each suffix.

`--lang` and `--under` narrow what a search *returns*. They never change what is
indexed, so a narrowed query cannot shrink the index:

```sh
ish "how is ranking done" --lang python
ish "installation steps" --lang markdown asciidoc
ish "parse a header" --under '/include/'
```

User-level defaults go in `~/.config/ish/config.toml`, which honors
`XDG_CONFIG_HOME`. Later sources win:

```
defaults
  < ~/.config/ish/config.toml
  < .ish/config.toml            (every one from the target path upward,
                                 or the one file --config names)
  < ISH_* environment
  < command line
```

A flat `ish.toml` is still read, beside `.ish/config.toml` and in the user
directory alike, so a file written before this keeps working. Prefer
`.ish/config.toml`: it keeps a tree's settings beside anything else the tool
leaves there.

**Every** config file from the target path upward applies, outermost first, and
each settles only the keys it names. So a file beside a subtree adds to the one
above it rather than replacing it — a subtree can set `git = false` for itself
and still inherit the `type_patterns` the repository above it set.

Set any option from the environment with the `ISH_` prefix, for example
`ISH_LIMIT=20` or `ISH_IGNORE=build,dist`.

### Name one config file

Point ish at a file with `--config PATH`, its short form `-c`, or the
`ISH_CONFIG` variable:

```sh
ish "how is ranking done" --config ~/work/firmware.toml
ISH_CONFIG=~/work/firmware.toml ish -i 30.Firmware/
```

The named file stands in place of the files above the tree, which ish then
does not look for. Your user file still applies below it, so a machine-wide
preference survives a file chosen for one run. The flag wins over the
variable. A named file that is not there is an error: a mistyped path would
otherwise run on the defaults and say nothing about it.

### Share a file with other tools

A config file may keep the options under a `[tool.ish]` table, where
`[tool.black]` and `[tool.ruff]` also live, so one file can hold sections for
several tools:

```toml
[project]
name = "firmware"

[tool.black]
line-length = 88

[tool.ish]
limit = 10
ignore = [".git", "build"]
```

ish reads that table alone then, and passes over every other section. A file
with no `[tool.ish]` table is a flat list of options, as above, so a file
written before this keeps working.

ish does not look for a `pyproject.toml` on its own. Name one with
`--config` or `ISH_CONFIG` to have its `[tool.ish]` table read.

## Extend

Two things are made to be added: a language and an embedding backend.
Each has one folder, one table, and a recipe at the top of the folder's
`__init__.py`.

**A language** is a class with a `language`, a set of `suffixes`, and a
`parse(path, source)` that returns chunks. Put it in a new module under
`src/ish/adapters/parser/` and add one line to `PARSERS` in that package's
`__init__.py`:

```python
PARSERS = {
    ...
    "toml": Language(TomlParser, aliases=frozenset({"tml"}), category="config"),
}
```

That line is the whole registration. It says how to build the parser, what
else a reader may call the language, and whether it holds code, prose, or
configuration. File discovery, the `--languages` and `--lang` choices, the
chunk size cap and the registry tests all follow from it. Nothing else needs
editing.

A parser that belongs to one project rather than to ish goes in
`~/.config/ish/parsers/` as a module with a `parser()` function. It joins the
same table at run time, may declare `aliases` and a `category` of its own, and
may replace a built-in language.

**A backend** subclasses `PrefixingEmbedder`, implements `_embed(texts)` and
a `from_option(model)` classmethod, and takes one line in `EMBEDDERS` in
`src/ish/adapters/embedder/__init__.py`. The `--embedder` choices derive from
the keys. A backend that needs a package the default install does not carry
is an extra in `pyproject.toml`, and a model trained with task prefixes gets
a row in `prefixes.py`.

## Develop

```sh
uv run poe check   # lint, typecheck, test
```

The architecture is ports and adapters. `.claude/CLAUDE.md` describes the
layers, the composition root, and the decisions behind them.
