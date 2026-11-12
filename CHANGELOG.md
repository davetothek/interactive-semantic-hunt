# Changelog

Everything a reader of the tool would want to know about a release, written
when the change is made rather than scraped from it afterwards.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the version numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Commands. `ish search QUERY [PATH]` searches, `ish ask QUESTION [PATH]`
  answers a question, and `ish pick [PATH]` opens the picker. `ish ask`
  takes `--brief`. Each command takes every option, and `--help`.
- `ish mcp`, `ish serve`, and `ish complete` start what `ish-mcp`,
  `ish-serve`, and `ish-complete` start. The three old names stay.
- A `sources` tool on a server over HTTP. It returns the chunks a search
  finds with the text of each, and the meaning of each acronym the texts
  use. A client reads it to write an answer with a model of its own. The
  tool runs no language model. A stdio server does not list it.
- `ish remote search QUERY` prints the results of a server over HTTP.
  `--limit`, `--format`, `--lang`, `--under`, and `--type` travel with
  the query.
- `ish remote ask QUESTION` answers from the tree of a server with the
  language model of this machine. The server returns chunks and runs no
  model. `--brief` gives the short answer, and `--ask-turns N` lets the
  model search the server again. The last line of stderr names the
  server and its version.
- `--url`, then `ISH_REMOTE`, then `remote` in the user config file names
  the server of `ish remote`. Its token is in `ISH_MCP_TOKEN`.
- `auth` names the ways a server over HTTP admits a request. `token`, the
  default, admits the token in `ISH_MCP_TOKEN`, as before. An empty `auth`
  stops the server with an error that names it.
- `ish remote login` signs in to the server, and `ish remote logout` signs
  out. A server that names a sign-in method in `auth` lists it at `/login`,
  beside `/mcp`, and gives a login token that lasts `login_days`, 30 by
  default. The client asks for the password with no echo, sends it in the
  body of one request, and keeps it nowhere. It keeps the token in
  `~/.config/ish/logins.json`, which only you can read, and every command
  of `ish remote` sends it when `ISH_MCP_TOKEN` is not set. A password goes
  only over https, or to a server on this machine.
- `ldap` in `auth` signs users in against an LDAP directory, with
  `ldap_url`, `ldap_user_dn`, and `ldap_starttls`. With `ldap_group`, only
  a member of that group gets a login token. A directory has 10 s to
  answer. It needs
  the `ldap` extra, which installs python-ldap. The user name is escaped
  before it reaches the directory, and an empty password never binds. A
  plain `ldap://` URL with no StartTLS starts with one warning.
- `ldap_bind_dn`, `ldap_base`, and `ldap_user_filter` sign users in
  through a service account, for a directory such as an Active Directory.
  The server binds as the account, finds the one entry of the user, checks
  `ldap_group`, and then binds as the user. The password of the account is
  in `ISH_LDAP_BIND_PASSWORD`, or in the file that
  `ISH_LDAP_BIND_PASSWORD_FILE` names, as Docker mounts a secret. Every
  option is also an `ISH_*` variable, so a container needs no config file.
- A server that signs users in listens on this machine only, behind a
  reverse proxy that holds the certificate, and refuses to start on
  another address. Five failed sign-ins for one user name in 15 minutes
  stop more attempts for 15 minutes, and the directory is not asked. A
  sign-in still in flight counts, so guesses sent at once do not pass the
  limit. The server answers 429 while it holds.
- A command that sends a token or a password follows no redirect, so the
  token goes only to the address you gave, and a plain http request goes
  to no proxy. A server error names the type of the error in its log, and
  never its text.
- A config file in a tree cannot set `auth`, `login_days`, or an `ldap_*`
  option, so a person who can commit to the tree cannot choose the
  directory that gets each password. `ish mcp --http` names such a file.
  The logins stay in the data directory whatever `cache_dir` says, and
  the server refuses a login file that another user can write.
- A server over HTTP serves at most 64 connections at once, closes one
  that sends nothing for 30 s, and checks at most 8 sign-ins at once. A
  user name has at most 256 characters, and a password 1024. A static
  token of fewer than 32 characters starts the server with a warning.
- A server keeps the SHA-256 of each login token, never the token, in
  `~/.local/share/ish/logins.jsonl`. The operator ends a login by deleting
  its line. Each tool call writes one line that names the user and the
  tool, never the query.
- `ish remote mcp` serves `search_code` and `ask` on stdio for the tree
  of a server, as `ish remote search` and `ish remote ask` do. An agent or
  an editor gets the tree of the server, and the language model of this
  machine writes each answer. `--url` names the server, as for the other
  commands of `ish remote`. A call takes no `path`. The reply of `ask`
  names the host and the version of the server.

### Changed

- The first word of a command line is a command when it is `search`,
  `ask`, `pick`, `mcp`, `serve`, or `complete`. To search for one of
  these words, use `ish search WORD` or `ish -- WORD`.
- A line without the word `remote` reads the tree on this machine, also
  when `ISH_REMOTE` or a config file names a server. `ish --ask` with
  such a server answers here and prints a warning that names
  `ish remote ask`.
- The Neovim and VS Code clients ask through `ish ask`, and answer from
  the local tree. An agent or an editor reads a server through `ish
  remote mcp`.
- A config file in a tree, at or above the working directory, does not
  name a server. ish ignores its `remote`, and `ish remote` prints a
  warning that names the file. A tree that named the server would choose where your token
  goes. Set `remote` in the user config file, or in a file you name with
  `--config`.
- `--remote URL` with `--ask` or `--brief` gets chunks from the server,
  and the language model of this machine writes the answer. This machine
  needs a `language_model` for it.

### Deprecated

- `--ask` and `--brief` on a line with no command. They work as before
  and print one warning that names `ish ask`. `-i` stays.
- `--remote URL` on a line with no command. It prints one warning that
  names `ish remote`. Use `ish remote` with `--url URL`.

### Fixed

- Quitting the picker while a search ran could fail on the way out with
  an error that no node matches `OptionList`. A search that ends after
  the picker closed now fills nothing.

### Removed

- The `ask` tool of `ish mcp --http`. A server over HTTP answers no
  question and builds no language model, so it needs no model and no API
  key. A client of an older release that calls `ask` gets "Unknown tool:
  ask". Use `ish remote ask`, which answers with the model of the machine
  that asks. A stdio server keeps its `ask`.

## 0.9.4 - 2026-11-10

### Added

- `--render` shows the answer of `ish --ask` rendered on a terminal: a
  table with its columns in line, lists, quotes, and each source as a link
  that opens with a click. Each citation is the number of its source and
  opens it. A pipe or a file still gets the Markdown. `render = true` in
  the configuration renders every answer, and `--no-render` turns it off
  for one question. Rich draws the answer. Textual installs Rich already,
  and ish loads it only to render.
- Ask from Neovim. `contrib/nvim/ish_ask.lua` asks a question through
  `ish --ask` and opens the Markdown answer in a split, so the `remote`
  server and the language model are the ones the command line uses. In the
  answer, `gf` opens the source under the cursor in the window the
  question came from.
- Ask from VS Code. `ish: Ask a question` in `contrib/vscode/` opens the
  answer as a Markdown document beside the editor.
- `contrib/nvim/ish_link.lua` follows a marker such as `[^1]` to its
  source, and a line of details to the source above it. `link()` returns
  the link the cursor points at, and `follow()` is the opener for `gf`: it
  leaves the name of a file, and a line with no link, to the built-in
  command.

### Changed

- `ish --ask` prints the answer as plain Markdown on a terminal again, as
  a pipe gets it. The colors of 0.9.3 made the markup harder to read. The
  timing table stays grey.

## 0.9.3 - 2026-11-09

### Added

- An answer names the acronyms it uses in a Terms section before the
  sources, each with what it stands for and a citation of the text that
  spells it out. A meaning comes from the results alone: the model lists
  the terms its sources spell out, ish keeps only those a source writes,
  and ish looks the rest up in what the tree defines, in text such as
  `Auto Exposure (AE)`, glossary tables, and description lists. A server
  collects the definitions of its whole tree when it starts. The `ask`
  tool sends them as `terms`.
- `--ask-turns N` travels with a question to a server, and the `ask` tool
  takes `ask_turns` from 0 to 5 for one question. Without it the server
  keeps its own setting. `ask_turns` is now an option of one question,
  like the filters.
- `ish --ask` colors the answer on a terminal: headings, markup, the gaps
  the documents leave, and source links that open with a click. It adds
  styles and changes no character, so a copy from the terminal is the
  Markdown a pipe gets.
- The time and tokens below an answer are a grey table after a blank
  line: one row for each stage, with its time, model, and tokens, then
  the total, and the version of ish and of the server that answered.
- `--color WHEN` covers the answer and the timing table as well as the
  logs, and `auto` honors `NO_COLOR`.
- `ish --help` starts with one line of usage and groups the options by
  what they do: search, answers, output, the index, embedding,
  maintenance, and the picker.

## 0.9.2 - 2026-11-09

### Added

- `ask_files` sets how many parsed files an answer keeps, 32 by default.
  With `ask_warm`, `ish-mcp` parses the files of its tree on a thread
  when it starts, so a server answers its first questions without
  parsing a file again. An answer on a 4-vCPU server spent 8 to 20 s
  parsing the AsciiDoc files it read.

- `ish --ask` and `ish --brief` end with one line on stderr that says
  where the time and the tokens went: the seconds of the search, of
  reading the chunks, of the rerank, and of the answer, with each model
  and its tokens. A remote answer also says how much of the wait the
  server spent. The `ask` tool of the MCP server sends the same as
  `timings`, `seconds`, and `rerank_model`.

### Fixed

- A K&R C function definition, which declares its parameters between the
  parameter list and the body, is read as a function. The C and C++
  parser dropped it with no message, and a file of such definitions
  alone failed to parse. A `.c` file that the C++ grammar cannot read
  cleanly is now read again with the C grammar, and that reading is kept
  when it holds fewer errors and loses no definition. Headers and modern
  C read as before.

## 0.9.1 - 2026-11-09

### Added

- `contrib/nvim/ish_link.lua` sends `ISH_MCP_TOKEN` as a bearer token
  when it fetches an https link, so the links of a server whose tree is
  behind that token open in Neovim. It sends the token only to the
  origin of `ISH_REMOTE` and to the origins in `token_origins`.

### Fixed

- A Markdown file lost every heading after a line of four or more
  hyphens or equal signs, such as a `----` thematic break or the `====`
  underline of a heading. The parser read the line as the start of a
  code block that never closed, so the file was one section. Markdown
  now opens a code block on backticks and tildes only. AsciiDoc blocks
  are read as before. The next refresh reads every file again and embeds
  only the sections it has not seen.

## 0.9.0 - 2026-11-06

### Added

- `ish --ask` and `ish --brief` can ask an `ish-mcp --http` server. Name
  it with `--remote URL` or `remote` in the config file, and put its
  token in `ISH_MCP_TOKEN`. The server searches its own tree and writes
  the answer, so the machine that asks needs no index and no model. A
  plain search and `-i` still read the local tree.
- The `ask` tool of the MCP server adds the answer as Markdown, the text
  `ish --ask` prints.
- `contrib/nvim/ish_link.lua` opens the source of an answer in Neovim, at
  the lines its link names. It reads a file URI from disk and an https
  link with curl, so the links of a server with `link_base` open too.

## 0.8.2 - 2026-11-06

### Added

- The documentation site has a page, "How it works", that describes the
  pipeline from scan to result: chunks, embedding, the SQLite index and
  the matrix file, vector and lexical search, and the fusion of the two.

### Fixed

- `ish-mcp --http` no longer warns about `ISH_MCP_TOKEN` at start. The
  settings read every `ISH_*` variable as an option, and logged the
  token as an unknown one three times beside a token that worked.
- An index run no longer prints the `SyntaxWarning`s of the Python files
  it reads. A warning such as an invalid escape belongs to the author of
  the file. One helper script in a documentation tree printed 13.

## 0.8.1 - 2026-11-02

### Fixed

- A long AsciiDoc specification with many includes no longer reads as a
  generated file. `max_chunks` counts a file's own sections and not the
  `table` chunks its includes bring in. One specification of 39
  sections and 900 includes yielded 1,005 chunks, and was indexed as
  one chunk under the default limit of 1000.

## 0.8.0 - 2026-11-02

### Added

- `ish --brief "question"` answers in a sentence or two, as plain text
  with no citations, for a script or a shell prompt. The model is told
  to write the short answer alone, so it writes about a third of the
  output tokens of a full answer, in about half the time. The `ask` MCP
  tool takes `brief` too. `brief.md` holds the instruction, and
  `prompt_dir` can replace it.

## 0.7.0 - 2026-10-31

### Changed

- AsciiDoc files are read as a reader sees them. An `include::`
  resolves, with attribute paths and tagged regions, so a section that
  assembles a register table from other files holds its rows and their
  values. A table that fits stays in one chunk, and a longer one keeps
  its header row in every chunk it divides into. A figure reads as a
  line of text with its alt text or file name. An include that brings
  in more than a few lines, such as a register table, is a `table`
  chunk of its own on its lines, and the section keeps its prose in
  another, so a question about the prose still finds it. The chunking
  stamp is raised, so the next refresh reads every AsciiDoc file again
  and embeds only the text it has not seen.

### Added

- `ish --ask "question" PATH` answers a question from the chunks a search
  finds, in Markdown: the question as a heading, the answer with a
  Markdown footnote for each claim, `[^1]`, and the cited sources as
  footnotes with links that open each file at its lines. It pipes into an editor as a document.
  An answer opens with a short answer, gives each part of the question
  a section, a procedure numbered steps, and registers or values a
  table, and gathers what the documents leave open in one block. Each
  source shows its file and lines, its type, its heading, and its
  directory. The `ask` MCP tool returns the same layout as data. A
  table keeps to a few words a cell and leaves out a column with no
  values, so it reads in a terminal. When the documents do not answer
  it, ish says so, lists the closest sources, and exits 2. A language
  model writes the answer, and it is off by default: set
  `language_model`, `answer_model`, and `allowed_language_models`. The
  `anthropic` backend reads its key from `ANTHROPIC_API_KEY`. The
  `ollama` backend runs a generation model on the local daemon and
  refuses a host that is not this machine. The `claude-cli` backend runs
  the Claude Code CLI on the account it is logged in to, with every
  tool, MCP server, and settings file off. It suits one person on their
  own machine, not a shared server. A search never builds a language
  model.
- `rerank_model` lets a cheap model of the same backend pick the ten
  chunks an answer reads from a wider search of `rerank_candidates`, 50
  by default, from the start of each chunk. Measured on 25 questions
  over a private specification tree, the expected source reached the
  answer for 21 of 21 questions against 16 of 21, and every answer
  cited it.
- `ish --ask` shows on stderr what it is doing while the models work,
  with the seconds each step has run: searching, choosing what to read,
  reading, writing the answer, and each follow-up search, which stays on
  the screen as a notice. stdout holds
  only the answer, so it pipes into an editor. Ctrl-C stops it with
  exit code 130 and no traceback, and stops a `claude` call with it.
- `ask_turns` lets the model ask for up to three follow-up searches a
  turn, for that many turns, before it answers. ish runs them with the
  same filters and adds what they find within the same budget. The
  model sees only what ish sends, so it works on a tree of documents
  alone. `ish --ask` lists the searches below the sources, and the
  `ask` tool returns them with the number of turns.
- The instructions a language model receives are text files that ship
  with ish. `prompt_dir` names a directory whose files replace them one
  by one, so an operator changes what a model is told without changing
  code. The limits an instruction names come from the code.
- The MCP server offers an `ask` tool. It returns JSON: the answer with
  `[n]` markers, and per citation the path, the location as data, and a
  link that opens the source. When the documents do not answer the
  question, `covered` is false and `sources` lists the closest chunks.
- `ish-mcp --http HOST:PORT` serves the same tools over MCP Streamable
  HTTP at `/mcp`, for clients on other machines. Every request carries
  the token in `ISH_MCP_TOKEN` as a bearer token, and the server refuses
  to start without one. A bare port listens on `127.0.0.1` only.
- `link_base` publishes the links of an answer under a base URL, such as
  `https://docs.example.com/spec/a.adoc#L120-164`. Empty links to the
  file on disk.

## 0.6.2 - 2026-10-29

### Changed

- An index run leaves the matrix file beside the index when it closes.
  The first query after `ish --refresh`, after a refresh of `ish-serve`,
  or after a background refresh in the TUI or the MCP server no longer
  reads every vector. Measured on 49,740 chunks, that query cost
  2,684 ms and now costs 325 ms, cold, median of 5. The index run takes
  that work on: 8,072 ms becomes 9,347 ms.

### Documentation

- The README says how to install and use ish, and links to the rest. Every
  option, the clients, configuration, the index, and extending ish each
  have a page under `docs/`, published at
  <https://davetothek.github.io/interactive-semantic-hunt/>.

## 0.6.1 - 2026-10-24

### Added

- Support Python 3.15. The suite runs on 3.12, 3.13, 3.14, and 3.15.

### Changed

- Only `ish --version` reads the package metadata. Every run built the
  argument parser with the version string in it, and the import and the
  lookup cost 20 ms of a run, a tenth of a warm query. Measured on this repository,
  `ish --help` cost 120 ms and now costs 100 ms, cold, median of 10.
- The git filter reads whether a file sits in the repository from the text
  of the two paths. It takes the top of the checkout from the composition
  root, which asked git for it already. Asking about the 184 files of this
  repository cost 2.25 ms and now costs 0.09 ms. A query runs two git
  commands in place of three. Loading the filter cost 10.9 ms and now costs
  8.2 ms, in-process, median of 10.
- A `type:` filter sorts a path into `test` with string operations. A
  filtered search asks about every chunk it walks. Measured on 49,740
  chunks, a `type:doc` search cost 1,034 ms and now costs 752 ms, warm,
  median of 5. Every path is sorted as before, checked over 460,665 paths.

### Fixed

- A tree outside a git repository asked git once for every file on every
  query, because the filter did not keep the answer that there was no
  repository. Measured on 20 copies of this repository outside git, a
  query ran 2,742 git processes in 6,177 ms and now runs 3 in 353 ms,
  cold, median of 5.

## 0.6.0 - 2026-10-23

### Added

- Remove the stored index of a tree with `ish --forget PATH`. A worktree is
  made for one branch and deleted with it, and its index outlived it. An
  index whose tree is gone is now left out of every search and said so
  once, with the command that removes it. The vectors stay: a checkout at
  an older commit still needs the vectors of text the main checkout has
  since changed, and a vector costs a second of embedding to make again.
- Delete the vectors no index of a repository references with
  `ish --sweep PATH`, on purpose. Measured on one firmware tree, sweeping
  them took a worktree from 100 % reuse to 87 %, so nothing sweeps them as
  a side effect.
- An index records the repository and the checkout its tree belongs to,
  as git names them, beside the tree itself. Every worktree of one
  repository names the same repository, which is what lets a second
  checkout reuse the work of the first, and an index that has recorded
  them is opened without asking git again.
- The vectors of a repository live in one shared store, which every index
  of the repository attaches. A vector is keyed by the text it stands for,
  so a second checkout, such as a git worktree, finds the vectors of the
  first and embeds only what differs. Measured on /srv/mono, the firmware
  tree of two worktrees shares 100 % of its chunk hashes with the main
  index. An index from the release before moves its vectors into the
  shared store on its first opening and embeds nothing again. Chunk rows
  and file stamps stay in the index of each tree, because a result names
  a file to open. A sweep of the shared store asks every index of the
  repository before it deletes a vector.
- Keep the indexes of a tree current with `ish-serve`, the one process
  that fills them. It refreshes the tree as soon as it starts, then every
  `refresh_seconds` or when a client asks, and it answers no query. It
  writes a status file under `$XDG_RUNTIME_DIR/ish/` every few seconds,
  and every client reads the index itself: one that finds the status of a
  live server leaves the refreshing to it, and one that finds none, or a
  status nobody has written for 30 seconds, brings the index up to date as
  before. `ish "query" path` then walks and embeds nothing before it
  answers, and says what the server is doing while a refresh runs.
  `ish-mcp` starts no refresh thread for a tree the server keeps, and
  `refresh_index` asks the server instead. One server keeps a whole
  repository current, the indexes of its worktrees included, wherever
  they are on disk. A second `ish-serve` for any checkout of the
  repository exits at once, a server whose tree is deleted stops, and
  `--once` refreshes once and exits. Measured on a firmware tree of 6,266
  chunks, a cold query took 691 ms on its own and 274 ms beside a server.
- `ish "query" path` shows what it is doing while it brings the index up
  to date: a spinner, the step, and while the model embeds the rate and
  about how long is left.

### Changed

- A search goes first while an index run embeds. Ollama serves one embedding
  request at a time, and every keystroke in a picker waited for the request
  `ish-serve` had in flight. A query now leaves a marker under
  `$XDG_RUNTIME_DIR/ish/`, and an index run holds its next request back for
  2 seconds after the last query. Measured with 64 chunks embedding in
  requests of 8 beside a picker typing 0.3 s apart, a keystroke after the
  first cost 605 ms and now costs 23 ms. An index run also sends one text a
  request instead of 8, so the first keystroke waits for one text at most:
  601 ms → 115 ms beside chunks of about 250 tokens, and 3.5 s → 45 ms
  beside chunks of about 1000 tokens. The vectors are the same bit for bit
  at any request size. The run pays about a fifth of its throughput on
  small chunks, 64 chunks of about 250 tokens in 9.1 s against 7.5 s, and
  nothing on large ones.
- `index_status` counts the chunks by language again only when the index
  changed, or when `ish-serve` finished another refresh. Measured on a root
  over 8 indexes and 74,459 chunks, a call cost 981 ms and a repeated one
  now costs 2 ms, which is what a picker polling once a second paid.
- `ignore` names and `exclude` patterns judge the path from the top of the
  checkout the tree belongs to, written with a leading slash, never the
  directories above it. A tree kept inside a directory its own
  configuration ignores, such as a git worktree under `.claude/worktrees/`,
  was indexed as empty: every file carried `.claude` in its path. A pattern
  such as `/vendor/` still matches at any depth, `^/vendor/` names the top
  of the checkout, and outside a repository the whole path is judged, as
  before.
- A search of a parent reads only the indexes of the checkout it starts
  in. A worktree kept under the main checkout, as `.claude/worktrees/`
  keeps them, no longer joins a search of the main tree, and a refresh of
  the main tree leaves the worktrees to their own. A search from a
  directory above several checkouts of one repository shows each chunk
  once, from the checkout nearest the path asked about.
- A search of a parent counts the chunks below it without building each
  one. The indexes of sibling trees cannot share a chunk, so their counts
  add up. Measured on a root over 7 indexes and 72,995 chunks, the count
  cost 0.92 s of a 1.57 s query. A tree indexed inside another still lists
  its chunks to count each once.
- The vectors a search scores are kept in a file beside each index, named
  `-matrix`, and mapped into memory. Every process read every vector out of
  SQLite and joined the blobs into one matrix, which cost 310 to 460 ms for
  224 MB of vectors. Mapping the same bytes costs 18 ms. The file carries a
  stamp of the index state it was built from, so a stale one is read past
  and written again. SQLite stays the source of truth, and the file holds
  ids and vectors only.

### Fixed

- An index of a checkout no longer covers a worktree kept inside it. A
  search of a worktree under `.claude/worktrees/` read the index of the
  main checkout, whose tree contains the worktree, and a refresh wrote the
  worktree's chunks into it. An index covers a path only within one
  checkout.
- Say what to do when a remote Ollama daemon does not answer. The message
  told every user to start a daemon with `ollama serve`, which cannot help
  when `OLLAMA_HOST` names another machine. The message now names that
  machine and says how to embed on this one instead. A local host keeps the
  advice it had.

## 0.5.0 - 2026-09-23

### Added

- Read more of each chunk, with `--context-tokens N` or the `context_tokens`
  key. The chunk cap of 8,000 characters follows from the 2048-token window
  Ollama serves by default, and `nomic-embed-text` accepts 8192. The cap
  scales with the window, the window reaches Ollama and llama.cpp, and a
  vector made under a wider window is kept apart from one made under the
  default, so the change embeds every chunk again and the old vectors stay
  for a return. The default is unchanged until the retrieval gain is
  measured.
- Guard against a machine-generated file. A file that yields more than
  `max_chunks` chunks, 1000 by default, is indexed as one chunk under its
  own name, and the run says so once with the count. One 26.3 MB register
  map produced 32,768 chunks, hours of embedding for text nobody searches
  by meaning. Set `--max-chunks N` for a tree whose hand-written files are
  larger.
- Index one tree that git ignores while git still filters the rest, with
  `--unignore REGEX` or the `unignore` key. A pattern is anchored at the
  tree root, like `include`. `git = false` was all or nothing: reaching one
  ignored checkout also reached a 386 MB virtual environment, 903 MB of
  build output, and a 621 MB cache, each found one crash at a time.
- Search from VS Code. `contrib/vscode/` is an extension that drives the
  resident `ish-mcp` server from a QuickPick, the same shape as the Neovim
  client: live results as you type, a preview of the highlighted chunk, the
  index refreshed when the picker opens with its progress in the status
  bar, and Tab to finish a filter word. It ships on its own schedule.
- Complete a filter word over MCP, with `complete_filter`. It answers with
  the completed query and the candidates in one call, from the registries
  the resident server already holds. `ish-complete` gave the same answer
  through a fresh process, which cost about 107 ms a Tab, nearly all of it
  interpreter start.
- Finish a filter word with Tab in the picker. `ty` becomes `type:` and
  `lang:cp` becomes `lang:cpp`, as in Neovim, and a word with several
  answers names them. The query field keeps focus.
- Finish a filter word in the shell. `contrib/shell/ish.bash` and
  `contrib/shell/_ish` bind `ish-complete` for bash and zsh, so
  `ish lang:cp<Tab>` becomes `ish lang:cpp` on the command line.
- Name the config file to read, with `--config PATH`, `-c`, or `ISH_CONFIG`.
  The named file stands in place of the files ish looks for by walking up
  from the tree. The user file below it still applies, so a machine-wide
  preference survives a file chosen for one run.
- Read the options from a `[tool.ish]` table when a config file holds one,
  so one file can carry sections for several tools. ish passes over
  `[tool.black]` and every other section beside its own. A file with no
  `[tool.ish]` table is a flat list of options, as before. The README shows
  both, beside the order ish reads the sources in.
- Choose the theme the picker draws in, with `--tui-theme NAME` or the
  `tui_theme` key. Textual holds a theme for a light terminal as well as for
  a dark one. An empty value keeps the Textual default, and a name no theme
  answers to is reported in the picker.
- Change the theme while the picker runs, with `ctrl+t`. Each press steps to
  the next theme and names it. The choice lasts for the run, and `tui_theme`
  says which theme the next run starts in. Textual keeps its own switcher on
  the command palette, which the picker turns off to keep `ctrl+p` for the
  previous result.

### Changed

- A change to how files divide into chunks reaches an existing index on
  its next refresh. Staleness is per file, so the size cap added in 0.1.0
  applied only to files that changed, and applying it to an existing index
  meant `--reindex`, 5,632 new chunks on one tree. The index now records
  the chunking it was read under. A refresh that finds another reads every
  file again and embeds only text it has never seen. This release re-reads
  every file once, and re-embeds nothing.
- The picker and the resident server load the model as soon as their
  refresh is done, while nothing waits on them. A daemon unloads an idle
  model and loads it again on the next request, which costs seconds, and
  the first keystroke paid that.
- The Neovim and VS Code clients ask the server to refresh when a file is
  saved, once a search has started the server. An edit was searchable only
  on the server's next poll, up to `refresh_seconds` later, which is 30 s
  by default. The editor is the one place that knows a file was saved.
- The picker paints before it scans. It lists what the index held last time
  at once, answers a query from that while the staleness scan and the
  refresh run behind the query field, and lists again when they land. The
  refresh progress moves to the header, so it never covers a preview. On
  23,215 chunks the scan was most of a 0.77 s startup, spent before anything
  was on screen.

### Fixed

- A Markdown or AsciiDoc file with no heading made no chunks and said
  nothing, while it entered the index as a file and looked indexed. On one
  tree 2,844 files held no chunks. Such a file is now one chunk named after
  the file when it holds a line of prose, and stays out when it holds only
  attribute definitions, includes, conditionals, comments, and table rows.
  `index_status` and `Ish.status()` report how many files were read and
  yielded nothing.
- An index run wrote every chunk row after the last vector of the run, so
  a reader saw 0 files and 0 chunks for the whole of one 2 h 43 m run under
  `--reindex`, and a run that stopped kept its vectors and none of its
  rows. The rows of a file now land as soon as its vectors are stored,
  every 64 chunks, so a second process sees the run as it goes and a
  stopped run keeps what it earned.
- An `exclude` pattern rejected each file after the walk had reached it,
  while `ignore` kept the walk out of a directory altogether. One query at
  the root of a 589,968-file tree took 10 s with its heavy directories in
  `exclude` and 1.8 s with the same names in `ignore`. An `exclude` pattern
  that matches a directory now keeps the walk out of it too.
- An `include` pattern matched a path segment at any depth, so a whitelist
  of one directory also admitted every copy of it under a worktree or an
  artifact tree. On one firmware tree that was 168,763 files for a corpus
  of a tenth that. An `include` pattern is now anchored at the root of the
  tree it indexes. Write `(?:.*/)?name/` to take a name at any depth.
- A call against an index that another process was writing waited without a
  limit. One index run held a status call for 1800 s, which returned no
  error and no progress. A store now waits two seconds for the lock and then
  names the file that is held. A search over several indexes leaves out the
  one under a writer, says which one, and answers from the rest.
- `index_status` and `Ish.status()` no longer bring the index up to date
  first. A status call reported nothing for as long as the run it started.
  They now report what the index holds. Call `build_index` or `index()` for
  fresh numbers.
- The preview pane read the source in a dark Pygments theme whatever the
  picker was drawing in, so a light theme still showed a dark code block.
  The preview now follows the picker: `gruvbox-dark` under a dark theme,
  `gruvbox-light` under a light one.

## 0.2.2 - 2026-09-19

### Fixed

- An index run that met a dead socket waited on it for hours while the
  daemon stayed healthy. A batch the daemon does not answer is now sent
  again, up to three times with a growing wait. A query is still sent once,
  because somebody is waiting on it.
- A generated document with thousands of values too large for the embedding
  window logged one warning for each of them, 12,795 lines in one run. The
  parser now reports once per file: how many, and the largest by name.

### Changed

- Keep everything about a language on the line that registers it. The names a
  reader may type for a language, such as `c` for the C++ parser, and whether
  it holds code, prose, or configuration, were tables away from the parsers
  they described. They are now fields on the registry entry, so adding a
  language is one module and one line. A parser a user wrote may name its own
  aliases and category the same way.

## 0.2.1 - 2026-09-18

### Added

- Say where a language or an embedding backend plugs in. Each adapter package
  opens with the recipe, the README has an Extend section, and the registry a
  new entry goes into sits beside the parsers or backends it lists.

## 0.2.0 - 2026-09-17

### Changed

- Bring the index up to date once per session in the Python API, on the first
  question, rather than on every call. Call `index()` to bring it up to date
  again. `chunks()` reads filter words out of a query line, and `scan()` lists
  what is on disk without an embedding backend.
- Show the embedding rate beside the count while `--refresh` runs, so a
  blocked run and a slow one no longer look the same.

### Fixed

- Apply `--type`, `--under` and `--lang` before the top slice of the ranking,
  so a narrow filter still fills its page. A filter applied after the slice
  returned nothing at a limit of 20 and two results at 100.

## 0.1.4 - 2026-09-07

### Changed

- Answer a search sooner while an index run is going. The daemon serves one
  embedding request at a time, so a query waits for the request in flight;
  sending fewer texts per request cuts that wait from about 97 s to about
  12 s. The whole run costs the same, and the vectors are unchanged.

### Fixed

- Report a busy embedding backend rather than waiting ten minutes for it. A
  query now says that an index run holds the daemon, and says it in a minute.

## 0.1.3 - 2026-09-07

### Fixed

- Stop writing to an index that is only being opened. A search, a status
  report, and a refresh of a tree nothing changed all wrote the tree path
  back over itself, which queued them behind any run that was indexing and
  made every other process rebuild the scored matrix it held.
- Read a file the parser rejects once, rather than on every refresh. Such a
  file is now recorded as holding nothing, and is read again when it changes.
  A file that could not be read at all is still asked about on the next run.

## 0.1.2 - 2026-09-05

### Added

- Keep this changelog. `poe release` names the `Unreleased` heading for the
  version it cuts, and refuses a release that has nothing under it.
- Publish a GitHub release beside the upload to PyPI, carrying the wheel, the
  sdist, and the notes for that version.
- Group the generated release notes under the labels the repository already
  uses: `indexing`, `speed`, `interfaces`, and `project`.
- Ship `CHANGELOG.md` in the sdist, and name it from the project links, so a
  reader who installs from PyPI can find it.

### Changed

- Refuse a pull request that changes the version unless it is a release. A
  version bump is its own pull request; it does not travel with the work that
  motivated it.

### Documentation

- Say how to keep generated code out of an index, with a worked pattern.
- Name the config file correctly. The documentation said `ish.toml`, which is
  read second for compatibility; the file to write is `.ish/config.toml`, and
  `~/.config/ish/config.toml` for a user default. Say also that every config
  file from the target path upward applies, each settling only the keys it
  names.

## 0.1.1 - 2026-09-05

### Fixed

- Size the interactive view to the terminal, not to a pipe. With stdout piped,
  `ish -i` read the wrong width and drew to it.

### Changed

- Cut a release with `poe release` rather than a workflow, so the tag is made
  where the version is set.
- Make the terminal-size tests fail when the code is wrong.

## 0.1.0 - 2026-09-03

### Added

- Search code by meaning, over four interfaces that share one index: the
  command line, an interactive picker, an MCP server, and a Python API.
- Keep the index in SQLite, one file for each tree. Key a vector by content and
  model, so a rename embeds nothing again and a change of model keeps both
  sets.
- Read Python, Markdown, AsciiDoc, C, and C++. A language is one module and one
  registry entry.
- Rank by a vector order fused with a BM25 order, gated to a query that names
  something.
- Narrow a search from the query itself, with `lang:`, `under:`, and `type:`.
- Configure from defaults, a user file, a project file, the environment, and
  the command line, in that order.
