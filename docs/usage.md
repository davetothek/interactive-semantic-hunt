# Use

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
to change the theme, `escape` to quit. The picker lists what the index held
last time before it looks for changes, and answers from that while the
refresh runs, with the progress in the header. Narrow without leaving the
query line:

```text
state machine transitions              every language
lang:cpp state machine transitions     the implementation
lang:yaml under:/10.System/ state      the tests that cover it
type:doc how do I configure this       the prose, not the code
type:test,doc retry backoff            the tests and what they document
```

Press Tab to finish a filter word, in the picker and in Neovim alike. `ty`
becomes `type:`, `lang:cp` becomes `lang:cpp`, and `under:/s` becomes
`under:/src/`; a word with several answers grows as far as they agree and
names the rest. `ish-complete` does the work from the command line, and
`complete_filter` does it over MCP, so any picker can call whichever it has.

The shell finishes the same words on the command line. Source
`contrib/shell/ish.bash` from `~/.bashrc`, or copy `contrib/shell/_ish` to a
directory on zsh's `$fpath`, and `ish lang:cp<Tab>` becomes `ish lang:cpp`.
Both call `ish-complete`, so the shell learns nothing about the languages.

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

## Options

| Flag | Purpose |
|---|---|
| `-i`, `--interactive` | Run the TUI picker |
| `-c`, `--config PATH` | Read the options from this file, not from the search upward |
| `--embedder {llama.cpp,ollama,st}` | Select the embedding backend (default: ollama) |
| `-v`, `-vv` | Increase log detail |
| `--color {auto,always,never}` | Control log color |
| `--limit N` | Maximum search results |
| `--ignore DIR ...` | Directory names to skip (default `.git .venv venv __pycache__`) |
| `--include REGEX ...` | Index only paths matching these patterns, anchored at the tree root |
| `--exclude REGEX ...` | Never index paths matching these patterns |
| `--git`, `--no-git` | Skip files git ignores (default: on) |
| `--unignore REGEX ...` | Index these paths although git ignores them, anchored at the tree root |
| `--lang LANG ...` | Show results only from these languages |
| `--under REGEX` | Show results only from matching paths |
| `--type TYPE ...` | Show results only of these kinds: `code`, `doc`, `test`, `config` |
| `--type-patterns TYPE:REGEX ...` | Say what a path holds, overriding the built-in reading |
| `--max-chunks N` | Index a file that yields more chunks than this as one chunk (default 1000) |
| `--context-tokens N` | How many tokens the model reads of each chunk (default 2048) |
| `--model NAME` | Override the backend model |
| `--refresh` | Bring every stored index at or below the path up to date first |
| `--reindex` | Discard the stored index and build it again |
| `--forget` | Remove the stored index of the path and stop. The vectors stay |
| `--sweep` | Delete the vectors no index of the path's repository references, and stop |
| `--no-cache` | Index in memory only, leaving nothing on disk |
| `--tui-theme NAME` | Theme for the picker (default: the Textual default) |

Logs go to stderr, so you can pipe stdout safely. A file that cannot be read
or parsed is counted in one line; `-v` names them.

## Theme the picker

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
