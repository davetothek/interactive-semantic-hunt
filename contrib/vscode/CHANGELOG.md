# Changelog

The VS Code extension ships on its own schedule. Its version is the one
in `package.json` here, and it says nothing about the version of the
`interactive-semantic-hunt` package on PyPI, which the server comes
from. The extension talks to whichever `ish-mcp` it finds on PATH.

## 0.1.0

### Added

- Search a workspace by meaning from a QuickPick, through one resident
  `ish-mcp` process per workspace folder. Filters typed into the query,
  such as `lang:cpp type:doc under:/src/`, are read by the server.
- Preview the highlighted chunk in the editor, with its lines selected,
  while the picker stays open. A cancelled picker puts the editor back.
- Refresh the index when the picker opens, and draw the progress in the
  status bar as `ish ███░░░░░ 38%`.
- Finish a filter word with Tab, and take a candidate from the head of
  the list with Enter.
- `ish: Search below this file's directory`, `ish: Refresh the index`,
  and `ish: Restart the server`.
- Refresh the index when a file is saved in a folder this window has
  searched, so an edit is searchable moments later.
