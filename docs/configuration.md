# Configure

Every command-line option is also a key in the config file, under the same
name. The one exception is `--config` itself: a file cannot name where to
find itself. A config file in a tree cannot set `remote`, the server of `ish
remote`, because a tree you cloned would choose where your token goes. It
also cannot set `prompt_dir`, `answer_notes`, or `rerank_notes`, because
a tree you cloned would choose what the model is told. Set them in the
user config file, `~/.config/ish/config.toml`, or in a file you name with
`--config`. Put project settings in `.ish/config.toml` at the root
of your repository:

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

## Leave tables out, and let the model grep them

Some generated files hold facts that a question needs, such as one table
for each item with its fields and values. Embedded, they crowd the top
results of a question about the design. Excluded, a search never returns
them, and a model does not know that they exist.

Name such files in `grepable`, with what they hold:

```toml
[[grepable]]
path = '(?:^|/)gen/tables(?:/|$)'
holds = "one table per item: fields, bits, access, reset value"
```

- The index leaves the files out, as `exclude` does. `path` is judged the
  way an `exclude` pattern is.
- An answer that can read the tree gets the list after its instruction to
  read, so the model can grep the files. An answer with no tools does not.
- The MCP server lists the entries in the `instructions` of its handshake,
  so an agent knows them. A server over HTTP does not, because its client
  has no file of the tree to grep.
- `index_status` and the `status()` of the Python API list the entries.
- Do not also put the path in `ignore` or `exclude`. The grepable entry
  keeps the files out of the index by itself. A walk that meets an ignored
  name or an exclude pattern at a grepable directory warns once for each
  entry.

On the command line and in `ISH_GREPABLE`, write an entry as one string,
`REGEX=WHAT THE FILES HOLD`. The first `=` ends the path, so a path in that
form cannot hold one. The environment takes one entry on each line.

The `holds` text reaches the model as a description, not as an
instruction. Write what the files hold, not what the model must do.

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

## Pull a missing embedding model

The Ollama daemon serves only the models that it has pulled. When it does
not have the embedding model, ish pulls it. The first search or index run
waits for the pull, and a model of a few hundred MB can take minutes.
To stop ish and get the command to run instead:

```toml
ollama_pull = false
```

ish pulls on the daemon that `OLLAMA_HOST`
names, which can be another machine. The `llama.cpp` and `st` backends
download their models themselves, so the option does not apply to them.

## Find one sentence of a long section

One vector stands for one chunk, and an embedding model averages what the
chunk says. A long section of a specification says many things, so its
vector stands for the topic of the section, not for one of its sentences.
`doc_windows` embeds each window of a few lines of a long document section,
about 400 characters with the heading path in front, as one more vector of
that section. A search gives a section the best score of its own vector and
its windows, and a result and an answer still read the whole section:

```toml
doc_windows = true
```

Measured on 10 questions over three files of a private specification tree:
top-1 for questions in plain words went from 3 of 7 to 5 of 7, and MRR from
0.464 to 0.833. A question that quotes one sentence of a 4,000-character
section found that section first, where it ranked 57th before.

A table gets no windows: a chunk with 70 % or more of its lines in table
cells names values rather than says things, and a window of its rows names a
few values. A tree of generated register tables is better left out of the
index altogether, with `exclude`, when the sections that use them include
their rows. On one specification tree, 290 such files held more than half
of the vectors, and took 156 of 240 places of 24 top 10s with windows.

A search with `--format text` shows the window that matched, so a reader
sees the sentence that answers, not the first lines of the section.

Code keeps one vector a chunk, because a definition says one thing. Windows
stay off by default: on this repository, which is mostly code, they gave
the documents more chances than the code and cost 8 points of top-1. A
section shorter than 800 characters gets no windows. Turning the option on
or off reads every file again, and embeds only the windows.

## Answer questions

`ish ask` and the `ask` MCP tool send the text of the chunks a search
finds to a language model. Nothing is sent until the configuration names a
backend and allows it:

```toml
language_model = "anthropic"
answer_model = "claude-sonnet-5-5"
allowed_language_models = ["anthropic"]
```

`allowed_language_models` is the gate. A backend that it does not name is
never built, even when `language_model` names it. There is no default
backend and no fallback: when the backend fails, the answer fails.

The `anthropic` and `ollama` backends need `answer_model`. The `claude-cli`
backend runs `sonnet` when `answer_model` is empty. claude resolves that
alias to its newest Sonnet, so the model changes when a new one is
released. Set a full ID, such as `claude-sonnet-5-5`, to keep one model. No call can change
these three options, because they decide where the text of a document goes.

| Backend | Where the text goes | Citations | Use it for |
|---|---|---|---|
| `anthropic` | The Anthropic Messages API, `api.anthropic.com` only. The key comes from `ANTHROPIC_API_KEY` | Native | A shared machine, or a key of a team |
| `ollama` | A generation model on the local daemon. A host that is not this machine is refused | From a JSON schema | Text that must stay on the machine |
| `claude-cli` | The Claude Code CLI, on the account it is logged in to, with every tool and settings file off | From a JSON schema | One person on their own machine |

An answer reads at most `ask_chunks` chunks, 10 by default, and at most
`ask_chars` characters of them over every turn, 60,000 by default. A
chunk that does not fit is skipped, and the answer counts it. Each
follow-up search adds at most `ask_search_chunks` chunks, 5 by default.

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

With the `claude-cli` backend, the model reads the files of the tree before
it answers. The sources of the search show it where to start: it reads the
section around a source, follows a reference to another file, and greps for
a name, with the Read, Grep, and Glob tools of `claude` and no other. It can
read no file outside the tree, and one call stops at a cost of one US dollar.
It cites a file it read as `path:first-last`, and ish reads those lines, so
the check of the claims finds each quote in them. `--no-answer-tools` turns
the reading off. The `ask` tool of a local `ish-mcp` on stdio reads the
tree the same way. An answer over HTTP, an answer from `ish remote`, and a
brief answer read no file.

`ask_turns` lets the model ask for follow-up searches before it answers,
up to that many turns. It reads the first sources, and when they fall short
or name a term worth finding, it sends up to three new queries. ish runs
them with the same filters, adds the best chunks it has not read, and asks
again. The model still sees only what ish sends, so this works the same on
a tree of documents with no code at all. `ish ask` lists the follow-up
searches below the sources.

```toml
ask_turns = 1
```

`--ask-turns N` sets it for one question. With `ish remote ask`, the model
of this machine runs the turns, and each follow-up search goes to the
server. The `ask` MCP tool takes from 0 to 5 per question.

Measured on 25 questions with the rerank and Haiku 5.5: 19 were answered in
one turn, so they cost the same. The model searched again for two hard
questions and for all four that the documents do not answer, and it still
refused those four. An answer cost about 0.4 cents against 0.3.

With a candidate of each type, 41 questions answered with Sonnet 5.5 used
no more than two turns. One hard question took a follow-up search, and
it was cited the same with `ask_turns = 1` as with 3, in 41 s against 63.
The four that the documents do not answer were refused in 20 to 25 s
against 22 to 40. Over those five questions, one turn cost a third less.

### Keep the files parsed

An answer reads the text of each chunk from its file, parsed again the
way the index parsed it, so an AsciiDoc section comes with its includes
resolved. A file with large includes takes seconds to parse. ish keeps
the last `ask_files` files parsed, 32 by default, so a second question
about the same files reads none again.

A server that serves one tree can keep the whole tree. With
`ask_warm`, `ish-mcp` parses the files of its tree on a thread when it
starts, up to `ask_files`, so the first questions read no file either. The
same pass collects what the acronyms of the tree stand for, which the
Terms section of an answer reads:

```toml
ask_files = 100000
ask_warm = true
```

Measured on a 4-vCPU server with a tree of AsciiDoc specifications:
reading the chunks of one answer took 8 to 20 s before. The text of a
whole tree of a few thousand files fits in tens of megabytes.

### Change what the model is told

The instructions a language model receives are text files that ship with
ish: `answer.md` for an answer, `answer_cited.md` for the `anthropic`
answer with native citations, `step.md` for a turn that may ask for
searches, `select.md` for the rerank, `check.md` for the check of the
claims, and `brief.md`, which `--brief` adds to the instruction that
writes the answer. Copy the ones to change into a
directory, edit them, and point `prompt_dir` at it:

```toml
prompt_dir = "~/.config/ish/prompts"
```

A file in that directory replaces the default of the same name. A file it
does not hold keeps the default. The lines of a paragraph join into one
line, so wrap them as you like, and a blank line starts a new paragraph.
`$max_gaps`, `$searches_a_turn`, and `$not_covered` are filled in by ish,
so an instruction always names the limit the code enforces. `step.md`
starts with `$answer`, the answer instruction. Write `$$` for a dollar
sign. A placeholder ish does not fill stops ish with an error that names
the file.

The JSON a reply must follow stays in code, because the parser reads it.
An instruction can ask for a different layout only within that schema.

To add to the instructions and change none of them, write notes. The
answering model reads `answer_notes` after its instructions, and the
rerank model reads `rerank_notes` after its own, each under a heading
that says the notes change none of the instructions above:

```toml
answer_notes = """
Answer in English, also when the question is in Norwegian.
A name such as REQ-1234 is a requirement id. Cite it as written.
"""
rerank_notes = "The specifications are under docs/spec and docs/system."
```

A note is sent as written, with no `$name` filled in. Prefer notes to
`prompt_dir`: a replaced file must keep every rule of the shipped one,
such as the rule to answer only from the sources, and a note cannot drop
one. Set notes, and `prompt_dir`, in the user config file or in a file
you name with `--config`. A config file in a tree cannot set them.

To compare two sets of instructions, run the evaluation with each:

```sh
uv run python scripts/evaluate_ask.py questions.yaml TREE \
  --language-model claude-cli --answer-model claude-haiku-5-5 \
  --prompt-dir ./prompts-b
```

To score `ish remote ask`, give `--remote URL` in place of the tree. The
server searches, and the model of this machine answers:

```sh
uv run python scripts/evaluate_ask.py questions.yaml \
  --remote https://ish.example.com/mcp \
  --language-model claude-cli --answer-model claude-haiku-5-5
```

A model gives a different answer from run to run. `--repeat 3` asks the
whole set three times and reports the mean and the range of each
measure. A model usage limit stops the run, and the questions after it
count as no miss. Each run appends one line to
`$XDG_DATA_HOME/ish/eval/history.jsonl`, and `--label NAME` names it.
To compare two runs, give two labels or line numbers:

```sh
uv run python scripts/evaluate_ask.py --compare baseline -1
```

A delta is real only when it is larger than the range of the two runs.

### Tune an answer

An answer of `claude-cli` makes several calls: one rerank, one call for
each turn of the answer, and one check for each version of the answer.
Each setting below changes one stage, and none needs a change to the
code. Turn a stage off to see what it costs and what it finds:

| Stage | Setting | Off |
|---|---|---|
| Choose what to read | `rerank_model` | `""` |
| Search again | `ask_turns` | `0` |
| Search against the draft | `challenge` | `false` |
| Read the tree | `answer_tools` | `false` |
| Check the claims | `check_claims` | `false` |
| Fix a claim that fails | `repair_turns` | `0` |

With all six off, an answer is one call. `ask_chunks`, `ask_chars`, and
`ask_search_chunks` set how much each call sends. `ask_searches` sets the
searches of one turn, `rerank_preview_chars` how much of each chunk the
rerank reads, and `check_context_chars` how much of the source around a
quote the check reads.

`claude-cli` takes more settings. `answer_effort`, `rerank_effort`, and
`check_effort` set the effort of each kind of call, from `low` to `max`.
Thinking counts as output tokens, so a lower effort costs less. Empty
uses the default of the model. `claude_fallback_model` names a model to
ask when the model of a call is overloaded. `claude_command` names the
command, and a config file in a tree cannot set it. `answer_timeout`
sets the seconds of one call, for every backend.

Each call of `claude` is a new process with a new message. claude keeps
the prompt in its cache, and a later call reads only the part that stays
the same, which is the instructions. `prompt_cache` sets how long the
cache lasts. A write for `5m` costs 1.25 times an uncached token, a
write for `1h` 2 times, and a read a tenth. `off` sends every token
uncached, and a call that reads the tree then pays in full for each
round of reading. `ish chat` and `ish remote chat` use
`chat_prompt_cache`, `1h` by default, since the next question can come
more than 5 minutes later.

claude marks the cache at the end of the instructions and at the end of
the message, and a later call reads only a part that ends at such a
mark. The sources of a turn come first in a message whose end changes
each turn, so a new call writes them again. With `resume_turns`, each
later turn of an answer that reads the tree resumes the session of the
turn before, and sends only the new sources and notes. The turn then
reads the sources, the files the model read, and its draft from the
cache. A chat is one conversation until it ends or `/new` starts another.
Each follow-up still searches, and sends only the chunks the session
does not hold, so the model reads the earlier sources, files, and
answers from the cache. A source keeps its id over the whole chat.
claude summarizes a chat session when it grows past
`chat_autocompact` tokens, 100,000 by default, and `autocompact` sets
the same for an answer, where 0 leaves it to claude. A question asked
again outside a chat always starts fresh. claude keeps each session
as a file under its config directory, and ish deletes it when the
process ends, when a new session takes its place, or past 8
conversations under way.

A cache can outlast a run. A second run within the hour reads what the
first one wrote, such as the rerank of the same question, and costs
less than a cold run. Compare runs made the same way.

Give the evaluation a setting with `--set`, or a file of settings with
`--config`. `--trace DIR` keeps every call of every question:

```sh
uv run python scripts/evaluate_ask.py questions.yaml TREE \
  --config tuning.toml --set challenge=false --trace trace/ --label no-challenge
```

Each call is one JSON file: the instructions, the message, the reply,
the tokens written to the cache and read from it, and the cost. Run one
call again alone, with one part changed:

```sh
uv run python scripts/replay_call.py trace/q07/005-check.json \
  --model claude-haiku-5-5 --repeat 3
```

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
