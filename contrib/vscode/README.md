# ish for VS Code

Search a workspace by meaning. Type what the code does, not what it is
called, and open the chunk that answers.

The extension is a client of `ish-mcp`, the Model Context Protocol
server that ships with [interactive-semantic-hunt](https://pypi.org/project/interactive-semantic-hunt/).
It is the same shape as the Neovim client in `contrib/nvim/`: one
resident server per workspace folder, started on the first search and
reused after, so a query costs a round trip and no process start.
Nothing here searches on its own.

## Setup

1. Install ish and a backend, as the [project README](https://github.com/davetothek/interactive-semantic-hunt#install)
   says. `ish-mcp` must be on PATH, or name it in the `ish.command`
   setting.
2. Install the extension: from the Marketplace, or from a `.vsix` built
   as described below.
3. Open a folder and press `Ctrl+Alt+I` (`Cmd+Alt+I` on a Mac), or run
   `ish: Search by meaning` from the command palette.

The first search of a tree indexes it, which takes a while on a large
one. The status bar shows how far it has got, and the results improve
as the index grows.

## Use

Type to search. Move the highlight and the chunk shows in the editor
beside the picker, with its lines selected. Press Enter to open it for
editing, or Escape to go back to where you were.

Narrow without leaving the query line, with the same words every ish
interface takes:

```text
state machine transitions              every language
lang:cpp state machine transitions     the implementation
type:doc how do I configure this       the prose, not the code
under:/src/ retry backoff              one subtree
```

Press Tab to finish a filter word. `ty` becomes `type:`, `lang:cp`
becomes `lang:cpp`, and a word with several answers grows as far as
they agree and lists them at the head of the results, where Enter takes
one. A QuickPick has no hook for a key inside its input, so Tab is
bound only while this picker is open.

| command | what it does |
|---|---|
| `ish: Search by meaning` | open the picker over the workspace folder of the file in view |
| `ish: Search below this file's directory` | the same, with `under:` set to the file's directory |
| `ish: Refresh the index` | bring the index up to date now, and show the progress |
| `ish: Restart the server` | stop every `ish-mcp` this window started |

Saving a file tells the server to refresh the index, once the tree has
been searched in this window, so an edit is searchable moments after
the save rather than on the server's next poll. `ish.refreshOnSave`
turns it off.

The server's stderr goes to the `ish` output channel.

## Settings

| setting | default | purpose |
|---|---|---|
| `ish.command` | `ish-mcp` | the command that starts the server |
| `ish.args` | `[]` | arguments for it |
| `ish.limit` | `40` | how many results one search returns |
| `ish.debounceMs` | `120` | how long typing must pause before a search is sent |
| `ish.preview` | `true` | show the highlighted chunk while the picker is open |
| `ish.refreshOnSave` | `true` | refresh the index when a file in a searched folder is saved |

What is indexed, which backend embeds it, and how paths are sorted into
`code`, `doc`, `test`, and `config` come from ish's own config file,
`.ish/config.toml` at the root of the tree. The extension cannot change
them, so no window can shrink an index another depends on.

## Build

```sh
cd contrib/vscode
npm ci
npm run check        # compile, then run the tests under node --test
npm run package      # npx @vscode/vsce package --no-dependencies
```

`npm run check` is what CI runs. The tests drive the client against a
fake server that speaks the protocol, so they need no ish install.
`npm run package` writes `ish-<version>.vsix`, which
`code --install-extension ish-<version>.vsix` installs.

## Release

The extension ships on its own schedule, independent of the PyPI
package. Raise `version` in `package.json`, add the entry to this
directory's `CHANGELOG.md`, then:

```sh
npx @vscode/vsce publish --no-dependencies
```

Publishing needs a Marketplace personal access token for the
`davetothek` publisher, which lives with the owner and nowhere in this
repository. The extension talks to whichever `ish-mcp` is on PATH, so
an extension release needs no PyPI release and a PyPI release needs no
extension release, unless a tool the extension calls changed shape.
