# Configure

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

`include` and `exclude` take regular expressions rather than globs, so
alternation works. `exclude` is searched against the path from the top of the
checkout the tree belongs to, written with a leading slash, so `/vendor/`
matches at any depth and `^/vendor/` only at the top. Outside a repository it
is searched against the whole path. `include` is anchored at the root of the
tree, so `core/` indexes that directory and not
`artifacts/core/`. Write `(?:.*/)?core/` to take the name at
any depth. `exclude` wins over `include`.

Both `ignore` and `exclude` judge only what lies inside the checkout, never
the directories above it. A git worktree kept under `.claude/worktrees/` is
indexable with the same configuration that keeps `.claude` out of the main
index.

`--git` is on by default, so anything a `.gitignore` covers stays out of the
index. Pass `--no-git` to index it all anyway, or name the one ignored tree
that belongs in the index with `unignore`, anchored at the root like
`include`, and let git keep filtering the rest:

```toml
unignore = ["vendor_spec/"]   # a checkout of another system, hidden from git
```

## Keep generated code out

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

Three things to know:

- `exclude` is index scope, so it decides what is *in* the index. Narrowing it
  later does not remove what a wider run already stored; run `--reindex` for
  that.
- A pattern is a regular expression searched against the whole path, so
  `/generated/` matches at any depth and needs no wildcards.
- A pattern that matches a directory, written with its trailing slash, keeps
  the walk out of it. `/build/` never enters `build`, so a tree of 272,364
  generated files costs nothing at all. A pattern that names files, such as
  `_pb2\.py$`, still reads every directory to find them.

Check a pattern before you pay to index it. An empty query lists what the
filter allows, and `--no-cache` keeps the trial out of the stored index:

```sh
ish "" . --no-cache | wc -l                          # everything today
ish "" . --no-cache --exclude '/generated/' | wc -l  # what the pattern leaves
```

If most of a tree is generated, it is usually less work to exclude the
directory than to name each suffix.

A file that slips past every pattern is caught by its size in chunks. A file
that yields more than `max_chunks` of them, 1000 by default, is indexed as
one chunk under its own name, and the run says so once. A file that large is
generated: the largest hand-written file measured split into 374 pieces, the
register map into 32,768. Raise the limit for a tree that is different.

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

## Read more of each chunk

A chunk is capped at 8,000 characters because Ollama serves an embedding
model a window of 2048 tokens, and a model drops what lies past its window
with no signal. `nomic-embed-text` accepts 8192. Ask for it, and the cap
follows to 32,000 characters, so a chunk carries about four times the
meaning:

```toml
context_tokens = 8192
```

A model given more of a text produces another vector for it, so the change
embeds every chunk again under the new window, and the vectors made under
the old one stay for a return to it. Measure before paying that:
`/benchmark` in this repository reports top-1 and MRR for a query set, and
a wider window is worth its re-index only when the table moves. The
`llama.cpp` backend takes the window the same way. The `st` backend reads
it from the model card, so there only the cap follows.

## Answer questions

`ish --ask` and the `ask` MCP tool send the text of the chunks a search
finds to a language model. Nothing is sent until the configuration names a
backend and allows it:

```toml
language_model = "anthropic"
answer_model = "claude-sonnet-5-5"
allowed_language_models = ["anthropic"]
```

`allowed_language_models` is the gate. A backend that it does not name is
never built, even when `language_model` names it. There is no default model
and no fallback: when the backend fails, the answer fails. No call can change
these three options, because they decide where the text of a document goes.

| Backend | Where the text goes | Citations | Use it for |
|---|---|---|---|
| `anthropic` | The Anthropic Messages API, `api.anthropic.com` only. The key comes from `ANTHROPIC_API_KEY` | Native | A shared server |
| `ollama` | A generation model on the local daemon. A host that is not this machine is refused | From a JSON schema | Text that must stay on the machine |
| `claude-cli` | The Claude Code CLI, on the account it is logged in to, with every tool and settings file off | From a JSON schema | One person on their own machine |

An answer reads at most ten chunks and at most 60,000 characters of them.
A chunk that does not fit is skipped, and the answer counts it.

A search ranks the right source too low when other chunks share the
wording of the question, such as test cases of the rule the question
asks about. `rerank_model` lets a model of the same backend look at the
start of each of `rerank_candidates` chunks, 50 by default, and pick the
ten worth reading in full:

```toml
language_model = "anthropic"
answer_model = "claude-haiku-5-5"
rerank_model = "claude-haiku-5-5"
allowed_language_models = ["anthropic"]
```

Measured on 25 questions over a private specification tree: the expected
source reached the answer for 21 of 21 questions with the rerank, against
16 of 21 without. It costs one more call of about 14,000 input tokens, and
with Haiku 5.5 for both calls an answer costs about a third of a cent at
API prices.

`ask_turns` lets the model ask for follow-up searches before it answers,
up to that many turns. It reads the first sources, and when they fall short
or name a term worth finding, it sends up to three new queries. ish runs
them with the same filters, adds the best chunks it has not read, and asks
again. The model still sees only what ish sends, so this works the same on
a tree of documents with no code at all. `ish --ask` lists the follow-up
searches below the sources.

```toml
ask_turns = 3
```

Measured on 25 questions with the rerank and Haiku 5.5: 19 were answered in
one turn, so they cost the same. The model searched again for two hard
questions and for all four that the documents do not answer, and it still
refused those four. An answer cost about 0.4 cents against 0.3.

`link_base` publishes the links of an answer under a base URL. With
`link_base = "https://docs.example.com/tree"`, a chunk at lines 120 to 164
of `spec/a.adoc` under the tree links to
`https://docs.example.com/tree/spec/a.adoc#L120-164`. Without it, a link is
a `file://` URI.

## Name one config file

Point ish at a file with `--config PATH`, its short form `-c`, or the
`ISH_CONFIG` variable:

```sh
ish "how is ranking done" --config ~/work/firmware.toml
ISH_CONFIG=~/work/firmware.toml ish -i core/
```

The named file stands in place of the files above the tree, which ish then
does not look for. Your user file still applies below it, so a machine-wide
preference survives a file chosen for one run. The flag wins over the
variable. A named file that is not there is an error: a mistyped path would
otherwise run on the defaults and say nothing about it.

## Share a file with other tools

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
