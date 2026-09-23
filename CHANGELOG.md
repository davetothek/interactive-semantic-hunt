# Changelog

Everything a reader of the tool would want to know about a release, written
when the change is made rather than scraped from it afterwards.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the version numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

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
