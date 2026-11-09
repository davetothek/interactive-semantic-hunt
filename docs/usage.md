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
lang:yaml under:/system/ state      the tests that cover it
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
  "test:/[0-9_]*(Tests|Verification)/",
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

## Ask a question

`--ask` answers the query as a question, from the chunks a search finds:

```sh
ish --ask "how does the release branch get its version" docs/
```

ish prints Markdown a person reads from the top down: a short answer, a
section for each part of the question, numbered steps for a procedure, a
table for registers, addresses, or values, and one block for what the
documents leave open. Each claim carries a numbered citation, and each
source is a link that opens the file at its lines:

```markdown
# how do I trigger a software reset

**Short answer:** write 0x01 to CTRL_CMD, then 0x1 to CTRL_GO.[1][2]

### Procedure

1. Set CTRL_CMD to 0x01, the software reset command.[1]
2. Set CTRL_GO to 0x1 to trigger it.[1][2]

> **Not in the documents**
>
> - The register addresses are in an appendix the sources do not hold.[1]

## Sources

1. [startup.adoc:329-364](file:///srv/docs/spec/startup.adoc#L329-364)
   doc · Function Description > Reset By Communication
   spec/
2. [reset.yaml:21-61](file:///srv/docs/tests/reset.yaml#L21-61)
   test · Verify Reset By Communication
   tests/
```

It reads well in a terminal, and an editor shows it as a document:

```sh
ish --ask "how does the release branch get its version" docs/ | nvim -c 'set ft=markdown' -
```

In Neovim, `gx` on a link opens the source. With `link_base` set, the
links point at the published tree instead of the files on disk.

A language model writes the answer, and it reads only the chunks the search
returned. When they do not answer the question, ish says so, lists the
closest sources, and exits 2. It does not answer from anything else. The
filter words work in a question as in a query: `--ask "type:doc how is X
done"`.

An answer that uses acronyms names them in a Terms section before the
sources, each with what it stands for and a citation of the text that
spells it out. ish never takes a meaning from the model's own knowledge.
A meaning comes from a source the answer read, or from another chunk of
the tree that defines it, such as a glossary, and a term no text spells
out is left out. A server parses its whole tree when it starts, so its
glossary covers the tree. A local answer knows the acronyms that the
chunks it read define.

On a terminal the answer is in color: headings bold, the markup dim, the
gaps the documents leave in yellow, and each source link clickable. Only
styles are added, so text copied from the terminal is the same Markdown a
pipe or a file gets. `--color never`, or `NO_COLOR=1`, turns color off, and
`--color always` keeps it through a pipe, such as into `less -R`.

After a blank line, stderr shows where the time and the tokens went, stage
by stage, in grey, and the version of ish:

```text
stage    time  model                  in    out
search  0.9 s
read   19.8 s
rerank  5.1 s  claude-haiku-5-5    9,448     60
answer  8.8 s  claude-sonnet-5-5  24,422  1,642
total  34.6 s

ish 0.9.3
```

`read` is the time spent reading the chunks from their files, for the
rerank and for the answer. An AsciiDoc file is read with its includes
resolved, so a tree of large attribute files reads slowly the first time.
stdout holds the answer alone, so the table never reaches a pipe.

For the fact alone, ask with `--brief`. The model writes a sentence or two
and nothing else, and ish prints it as plain text with no citations, which
suits a script or a shell prompt:

```sh
ish --brief "how long is the watchdog period while streaming" docs/
```

Ask again without `--brief` for the sources. A brief answer costs about a
third of the output tokens and half the time.

### Ask a server

`--ask` and `--brief` can ask an `ish-mcp --http` server instead of the
tree on this machine. Name the server with `--remote`, or with `remote` in
the config file, and put its token in `ISH_MCP_TOKEN`:

```sh
export ISH_MCP_TOKEN="<the token of the server>"
ish --remote https://docs.example.com/mcp --ask "how is the watchdog configured"
```

The server searches its own tree and writes the answer with its own
language model, so this machine needs no index and no model. The filter
words and the `--lang`, `--under`, and `--type` flags travel with the
question, and so does `--ask-turns N` when you give it: `0` answers from the
first search, and up to `5` lets the model search again before it answers.
Without the flag the server uses its own `ask_turns`. The path argument does not, because a path on this machine
means nothing to the server. The exit codes are the same as for a local
answer. A plain search and `-i` still read the local tree.

The total is the seconds you waited, and beside it the seconds the server
spent. The rest is the network and the queue. The last line names the
version of the server as well. A server that sends no timings is too old
to time its stages, and the table gives its tokens alone.

The links in the answer are the ones the server makes. Set `link_base` on
the server, so they point at a published tree rather than at its disk. When
that tree is behind the same token, the [Neovim link
opener](clients.md#use-from-neovim) sends `ISH_MCP_TOKEN` with each fetch.
Set `ISH_REMOTE` rather than `--remote` so the opener knows the server.

Answers are off until you choose a model. See
[Answer questions](configuration.md#answer-questions). A plain search never
builds a language model and sends no text anywhere.

## Options

| Flag | Purpose |
|---|---|
| `-i`, `--interactive` | Run the TUI picker |
| `-c`, `--config PATH` | Read the options from this file, not from the search upward |
| `--embedder {llama.cpp,ollama,st}` | Select the embedding backend (default: ollama) |
| `-v`, `-vv` | Increase log detail |
| `--color WHEN` | Color the answer, the logs, and the timing table: `auto` (a terminal, unless `NO_COLOR` is set), `always`, or `never` |
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
| `--ask` | Answer the query as a question, with the sources it cites |
| `--brief` | Answer the query in a sentence or two, with no sources |
| `--language-model NAME` | Language model backend that writes answers: `anthropic`, `ollama`, `claude-cli` |
| `--answer-model MODEL` | Model that backend runs. Required, there is no default |
| `--allowed-language-models NAME ...` | Backends that may receive text. Empty allows none |
| `--rerank-model MODEL` | Model of the same backend that picks the chunks an answer reads |
| `--rerank-candidates N` | Chunks the rerank model chooses from (default 50) |
| `--ask-turns N` | Follow-up searches the model may ask for before it answers (default 0) |
| `--prompt-dir DIR` | Instruction files that replace the shipped ones, file by file |
| `--link-base URL` | Base URL where the tree is published, for the links of an answer |
| `--remote URL` | MCP server that answers `--ask` and `--brief`, with its token in `ISH_MCP_TOKEN` |

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
