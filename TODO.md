# 0.2.0

Everything here came out of using 0.1.0 on a large firmware tree. Each
item says what was measured, so a reader can decide whether it is worth
the work rather than take the claim on trust.

## Say what is built

- [ ] **`spec.md` describes a project that no longer exists.** It lists
      persistent indexing, incremental indexing, hybrid ranking, MCP,
      the Python API, Tree-sitter and non-Python languages as "still out
      of scope", and all of them shipped. `.claude/CLAUDE.md` had the
      same fault and was fixed at release; the spec was not. A reader
      who starts there is misled about what the tool does.

## Indexing

- [ ] **Guard against a machine-generated file.** One 26.3 MB JSON
      register map produced **32,768 chunks** and took 76 s to parse,
      which would swamp an index and cost hours to embed. A file that
      yields thousands of chunks is generated; index it whole, or skip
      it and say so. Deciding which is the work.

- [ ] **Read more of each chunk.** `MAX_CHUNK_CHARS` is 8,000, which is
      the 2048-token window Ollama serves by default. nomic-embed-text
      accepts 8192, so passing `num_ctx` would let a chunk carry about
      four times the meaning. This changes what a stored vector means,
      so it needs a `SCHEMA_VERSION` bump and a full re-index — measure
      the retrieval gain before paying that.

- [ ] **Re-chunk without rebuilding everything.** The size limit added
      in 0.1.0 applies only to files that change, because staleness is
      per file. Applying it to one existing tree meant `--reindex` and
      5,632 new chunks. A targeted pass over files whose chunks exceed
      the window would cost a fraction of that.

- [ ] **Let a tree exclude what it generates.** Generated headers were
      99% of the oversized C++ text on the tree measured. `exclude`
      already does this; what is missing is saying so in the
      documentation, with the pattern that finds them.

## Speed

- [ ] **Paint the interactive view before scanning.** Startup is 0.77 s
      and most of it is the staleness scan across every index under the
      path. The field already accepts typing from the first frame, so
      the scan could run after the first paint rather than before it.

- [ ] **The query embedding is now the largest cost per keystroke**, at
      50–120 ms against about 25 ms for the search itself. Ollama pins
      an embedding model to one slot, so concurrency does not help;
      a smaller model, or holding a warm batch, might.

- [ ] **Complete without starting a process.** `ish-complete` costs
      about 107 ms a Tab, nearly all of it interpreter startup. The MCP
      server is already resident and already knows the registries.

## Interfaces

- [ ] **Complete filter words outside Neovim.** `interfaces/complete.py`
      is interface-agnostic and only the editor uses it. Shell
      completion for bash and zsh, and a completion key in the TUI, are
      the same function bound twice more.

- [ ] **Notice an edit rather than poll for one.** A resident server
      re-checks every `refresh_seconds`, 30 by default, so an edit can
      take that long to appear. Watching the filesystem would make it
      immediate and cost nothing while nothing changes.

## Project

- [ ] **Keep a changelog.** There is none, and a second release is when
      that starts to matter.

- [ ] **Protect `main` and check the version before tagging.** The
      release workflow refuses a tag that disagrees with `pyproject`,
      but nothing stops a direct push to `main`.
