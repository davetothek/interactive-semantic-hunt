# Changelog

Everything a reader of the tool would want to know about a release, written
when the change is made rather than scraped from it afterwards.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the version numbers follow [Semantic Versioning](https://semver.org/).

## Unreleased

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
