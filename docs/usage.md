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

The answer is Markdown, on a terminal as in a pipe or a file, so text
copied from the terminal is the text of the answer.

With `--render`, a terminal shows the answer rendered: a table with its
columns in line, lists, quotes, and each source as a link that opens with
a click. Each citation is the number of its source, and it opens that
source. A pipe or a file still gets the Markdown, and `--brief` stays
plain text. Set `render = true` in the configuration to render every
answer, and `--no-render` turns it off for one question. With
`--color never`, or `NO_COLOR=1`, a rendered answer keeps its layout,
takes no style, and prints each URL beside its label.

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

`ish remote` reads an `ish mcp --http` server, not a tree on this machine.
Name the server with `--url`, with `ISH_REMOTE`, or with `remote` in the
config file, in that order, and put its token in `ISH_MCP_TOKEN`:

```sh
export ISH_REMOTE="https://docs.example.com/mcp"
export ISH_MCP_TOKEN="<the token of the server>"
ish remote search "watchdog period"
ish remote ask "how is the watchdog configured"
ish remote ask --brief "how long is the watchdog period"
```

The token is the one secret, and it goes in the environment. ish refuses
a URL that holds a user name or a password, because the URL names the
server in each error and on stderr.

`ish remote search` prints the result lines of the server. `--limit` and
`--format` travel with the query.

For `ish remote ask`, the server searches its own tree and returns the
chunks with their text. The language model of this machine picks the chunks
and writes the answer, so this machine needs no index and the server runs
no model. Choose the model on this machine, as for a local answer. With
`--ask-turns N`, the model can search the server again before it answers.

The filter words in the query travel to the server, and so do the `--lang`,
`--under`, and `--type` flags of the line. A filter from the environment or
from a config file of this machine stays here, and so does a path: the
server searches its own tree, and `ish remote` takes no path. The exit
codes are the same as for a local answer.

The table on stderr shows the stages of this machine. The last line names
the version of ish here, the version of the server, and its host:

```text
ish 0.9.4 · server 0.9.4 at docs.example.com
```

The links in the answer are the ones the server makes. Set `link_base` on
the server, so they point at a published tree rather than at its disk. When
that tree is behind the same token, the [Neovim link
opener](clients.md#use-from-neovim) sends `ISH_MCP_TOKEN` with each fetch.
Set `ISH_REMOTE` rather than `--url` so the opener knows the server.

Every line without the word `remote` reads the tree on this machine, with
`ISH_REMOTE` set or not. One exception stays for one release: `--remote URL`
with `--ask` or `--brief`, on a line with no command, asks that server and
prints a warning that names `ish remote ask`.

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
| `--color WHEN` | Color the logs, the timing table, and a rendered answer: `auto` (a terminal, unless `NO_COLOR` is set), `always`, or `never` |
| `--render`, `--no-render` | Render the answer of `--ask` on a terminal. A pipe still gets the Markdown |
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
| `--remote URL` | Server that `--ask` and `--brief` read on a line with no command, for one release. Use `ish remote` with `--url URL` |

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
