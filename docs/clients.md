# Clients

## Use from Neovim

`contrib/nvim/ish.lua` is an fzf-lua picker. Copy it to `lua/utils/ish.lua` and
bind it:

```lua
map('n', '<leader>fi', function() require('utils.ish').search() end,
    { desc = 'Semantic search (ish)' })
```

It reads `--format grep`, so the built-in previewer opens each result at its
line, and prints the rank in the leftmost column. `search_lang({'cpp'})`,
`search_type({'doc'})`, and `search_here()` narrow it, as does a `lang:`,
`type:`, or `under:` word typed into the query.

While the index refreshes, `require('utils.ish').statusline()` renders a bar
for a statusline — `ish ███░░░░░ 38%` — and an empty string when idle. It reads `vim.g.ish_index_status`, which the picker keeps up to date, and
shows `ish ✓` briefly when a refresh finishes. Nothing is reported through
`vim.notify`: with `cmdheight = 0` there is no command line to put a message
in, so nvim draws one over the last screen row — the statusline itself.

The picker never blocks the editor: results are written as they arrive, so
typing stays smooth however long a search takes.

Saving a buffer tells the server to refresh, once a search has started it,
so an edit is searchable moments after the save. The server would otherwise
notice on its next poll, up to `refresh_seconds` later.

`contrib/nvim/ish_server.lua` keeps one `ish-mcp` process per session. It
starts on the first search and is reused after that, which cuts a keystroke
from about 500 ms to about 150 ms. Copy it beside the picker.

`contrib/nvim/ish_link.lua` opens a source of an answer at the lines it
names. Copy it to `lua/utils/ish_link.lua` and bind it to `gf`:

```lua
map('n', 'gf', function() require('utils.ish_link').follow() end,
    { desc = 'Open the ish link, or the file under the cursor' })
```

`follow()` leaves the key to the built-in command where the cursor points
at no link. The name of a file under the cursor also stays with the
built-in command, on a line that holds a link as well. `open()` opens the
link and has no such fallback, and `link()` returns the link the cursor
points at, or nil.

The opener takes the link under the cursor, or the first link on the line. On a
marker such as `[^1]` in the prose it takes the source that the marker
names, and on a line of details below a source it takes that source. A file
URI opens the file. An http or
https link, which a server gives when it sets `link_base`, is read with curl
into a buffer named after the link, at the first line of its `#L` range, and
`:w` keeps a copy. curl reads `~/.netrc`, so a tree behind basic
authentication needs one line there. The fetch runs in the background, and
only a failure shows a message.

A tree behind the bearer token of the server gets `ISH_MCP_TOKEN` in an
Authorization header. The module sends the token only to the origin of
`ISH_REMOTE`, so a link to another host never sees it. Name more origins in
`token_origins`:

```lua
require('utils.ish_link').token_origins = { 'https://docs.example.com' }
```

curl reads the header from stdin, so the token is not on its command line.

`contrib/nvim/ish_ask.lua` asks a question and opens the answer in a split.
Copy it to `lua/utils/ish_ask.lua`, beside the link opener, and bind it:

```lua
map('n', '<leader>fa', function() require('utils.ish_ask').ask() end,
    { desc = 'Ask ish a question' })
```

It asks for the question and opens the split at once, with the seconds it
has waited. When `ish ask` returns, the split holds the answer as
Markdown. The model of this machine writes the answer, so the language
model and the filter words in the question are the ones a shell uses. See
[This tree, a server, or both](#this-tree-a-server-or-both) for where the
question goes. It needs `ish` on PATH. A call can give the question, the tree, and more flags:

```lua
require('utils.ish_ask').ask('how is the watchdog configured',
    { cwd = 'docs/', args = { '--brief' } })
```

In the answer, `gf` opens the source the cursor points at in the window
the question came from, so the answer stays in view. The module sets the
key on the buffer of the answer, where no global mapping of the same key
reaches. `split` and `link_keys` change the window and the keys:

```lua
local ask = require('utils.ish_ask')
ask.split = 'botright 15split'
ask.link_keys = { 'gf', '<CR>' }
```

### This tree, a server, or both

Both editor clients choose where a search and a question go. Set it with
`vim.g.ish_source` in Neovim, or `ish.source` in VS Code:

| value | where it goes |
|---|---|
| `auto` (the default) | the index of this tree when there is one, the server when one is set, and both when both are |
| `local` | the index of this tree only |
| `remote` | the server only, through `ish remote mcp` |

With both, the picker lists this tree first and the server after, and
marks each line of the server with `[server]`. Two indexes score on two
scales, so the lists stay apart. A line of the server opens the file of
the same path in this tree, which holds it when the server serves the same
repository. A question goes to this tree first, and when its documents do
not cover it, the client offers to ask the server.

The client asks `index_exists` whether this tree has an index, which
starts none. With no index and no server, or with `local` and no index,
the client asks once before it builds one, because a large tree takes
hours to embed. It keeps the answer for each tree until the editor starts
again. To build an index after a "Not now", run
`require('utils.ish').index()` in Neovim, `ish: Refresh the index` in
VS Code, or `ish index` in a shell. A save refreshes only a tree that is searched here.

## Use from VS Code

`contrib/vscode/` is an extension that drives the same `ish-mcp` server
from a QuickPick. Press `Ctrl+Alt+I` and type. The highlighted chunk shows
in the editor beside the picker, Enter opens it with its lines selected, and
Tab finishes a filter word. While the index refreshes, the status bar shows
`ish ███░░░░░ 38%`. `ish: Ask a question` runs `ish ask` and opens the
answer as a Markdown document beside the editor. It needs `ish-mcp` on PATH,
and `ish` for a question, like the Neovim client, and
[its README](https://github.com/davetothek/interactive-semantic-hunt/blob/main/contrib/vscode/README.md) says how to build, install, and
release it. It ships on its own
schedule, apart from the PyPI package.

## Use from Python

```python
from ish.interfaces.python.api import Ish

with Ish("src/") as ish:
    for chunk, score in ish.search("type:doc how to configure", limit=5):
        print(score, chunk.path, chunk.symbol)
    print(ish.status())
```

`Ish` holds the index open, so a second query costs a search rather than a
process start. It offers `search()`, `chunks()`, `index()`, `refresh_all()`,
and `status()`, and reads `lang:`, `type:`, and `under:` out of the query
exactly as the other interfaces do.

## Use from an agent

`ish-mcp` serves the same search over the Model Context Protocol, so an agent
can query the index directly. Add it to a project with `.mcp.json`:

```json
{
  "mcpServers": {
    "ish": { "command": "uv", "args": ["run", "ish-mcp"] }
  }
}
```

It offers `search_code`, `ask`, `list_chunks`, `index_status`,
`refresh_index`, `complete_filter`, and `index_exists`, which says whether
a tree has an index and starts none. `ask` answers a question from the
chunks a search finds and returns JSON: the answer with `[n]` markers, and
for each citation the path, the location, and a link. It needs a language
model, which [Answer questions](configuration.md#answer-questions) sets up. The server stays resident, so a query costs about 58 ms
rather than a process start, and a filter word completes from the registries
the server already holds rather than through a fresh `ish-complete`.

The server re-checks a tree every `refresh_seconds`, 30 by default. An
editor knows the moment a file is saved, so the Neovim and VS Code clients
call `refresh_index` then, and an edit is searchable moments later. A client
of your own should do the same.

A call may narrow one search with `lang`, `under`, `type`, and `limit`, or
write the same filters into the query text. It cannot change
what is indexed — those settings come from the config file only, so no single
call can shrink an index that another call depends on.

## Serve over HTTP

`ish-mcp` serves stdio to the client that starts it. For clients on other
machines, serve the tools over MCP Streamable HTTP instead:

```sh
export ISH_MCP_TOKEN="$(openssl rand -hex 32)"
ish-mcp --http 0.0.0.0:8765
```

Every request goes to `/mcp` and carries the token as
`Authorization: Bearer <token>`. A request without it, or with a wrong one,
gets 401 and runs nothing. The server refuses to start without a token.
`auth` in the config of the server names the ways it admits a request.
`token`, the default, admits the token in `ISH_MCP_TOKEN`. A
bare port, such as `--http 8765`, listens on `127.0.0.1` only. Each request
runs on its own thread, so a slow search for one client does not hold up
another. Run it in the tree it serves, beside an `ish-serve` that keeps the
index current.

A server over HTTP offers `sources` in place of `ask`. It answers no
question and builds no language model, so it needs no model and no API
key. A client of an older release that calls `ask` gets "Unknown tool:
ask". `sources` takes a `query`, and
`path`, `limit`, `lang`, `under`, and `type` as `search_code` does. It
returns JSON: `sources` holds the ranked chunks, each with its `path`,
`type`, `location`, `kind`, `symbol`, `language`, `score`, `url`, and
`text`, and `terms` holds the meaning of each acronym the texts use, with
the chunk that spells it out. The tool runs no language
model. A client reads it to write an answer with a model of its own. One
call returns at most 200 chunks.

### Sign users in

A server can give each user a login token of their own, in place of one
token for everyone. Name a sign-in method in `auth`, beside `token`:

```toml
auth = ["token", "ldap"]
login_days = 30
ldap_url = "ldaps://ldap.example.com"
ldap_user_dn = "uid={user},ou=people,dc=example,dc=com"
ldap_group = "cn=ish,ou=groups,dc=example,dc=com"
```

`ldap` binds to the directory as the user, with the name that
`ldap_user_dn` makes from the user name, and the password. The server
escapes the user name first. With `ldap_group`, only a member of the group
gets a login token. It needs the `ldap` extra, which installs python-ldap:

```sh
pip install 'interactive-semantic-hunt[ldap]'
```

Without it, the server does not start, and names the extra. Use an
`ldaps://` URL, which checks the certificate of the directory. A plain
`ldap://` URL sends each password in plain text: set `ldap_starttls =
true`, or the server starts with one warning.

#### With a service account

A directory where the name of a user cannot be made from the user name,
such as an Active Directory, needs a service account. The server binds as
the account, searches `ldap_base` for the one entry that
`ldap_user_filter` finds, checks `ldap_group` as the account, and then
binds as the user with the password:

```toml
auth = ["token", "ldap"]
ldap_url = "ldaps://ldap.example.com"
ldap_bind_dn = "cn=ish,ou=services,dc=example,dc=com"
ldap_base = "ou=people,dc=example,dc=com"
ldap_user_filter = "(sAMAccountName={user})"
ldap_group = "cn=ish,ou=groups,dc=example,dc=com"
```

`ldap_user_filter` is `(uid={user})` when it is empty. Set
`ldap_user_dn` or `ldap_bind_dn`, not both. The password of the account
goes in `ISH_LDAP_BIND_PASSWORD`, or in a file that
`ISH_LDAP_BIND_PASSWORD_FILE` names. No config file holds it. A search
that finds no entry, or more than one, refuses the user as a wrong
password does. A directory that refuses the account is a fault of the
server: the user reads that the directory failed, and the log names the
account.

#### From the environment alone

Each option is also an `ISH_*` variable, so a container needs no config
file. A list is the names with commas:

```sh
docker run \
  -e ISH_MCP_TOKEN \
  -e ISH_AUTH=token,ldap \
  -e ISH_LDAP_URL=ldaps://ldap.example.com \
  -e ISH_LDAP_BIND_DN=cn=ish,ou=services,dc=example,dc=com \
  -e ISH_LDAP_BASE=ou=people,dc=example,dc=com \
  -e "ISH_LDAP_USER_FILTER=(sAMAccountName={user})" \
  -e ISH_LDAP_GROUP=cn=ish,ou=groups,dc=example,dc=com \
  -e ISH_LDAP_BIND_PASSWORD_FILE=/run/secrets/ldap_password \
  ...
```

#### What the server refuses

A config file in the tree that the server serves cannot set `auth`,
`login_days`, or an `ldap_*` option, because a person who can commit to
the tree would choose the directory that gets each password. Set them in
`~/.config/ish/config.toml` of the user that runs the server, or in
`ISH_*` variables. The server names a file of the tree that sets one.

A server that signs users in listens on this machine only, such as
`--http 127.0.0.1:8765`, behind a reverse proxy that holds the
certificate. ish holds no certificate, so a password that reaches it over
the network would cross the network in plain text. The server refuses to
start on another address.

Five failed sign-ins for one user name in 15 minutes stop more attempts
for that name for 15 minutes. A sign-in still in flight counts, so
guesses sent at once do not pass the limit. The server answers 429 and
asks the directory nothing in that time. A user outside `ldap_group`
gets the same refusal as a wrong password, and the log of the server
says why. The directory has 10 s to answer.

The server lists the methods at `/login`, beside `/mcp`, with no token.
`ish remote login` posts a user name and a password there, and the server
sends back a login token that lasts `login_days`. Forward `/login` with
`/mcp` in the reverse proxy.

The server keeps the SHA-256 of each login token, never the token, in
`~/.local/share/ish/logins.jsonl`, one login a line: the hash, the user,
the method, and the expiry. A copy of the file opens nothing. To end a
login, delete its line. The next request with that token gets 401. Each
tool call writes one line to the log that names the user and the tool,
never the query.

Point a client at it:

```json
{
  "mcpServers": {
    "docs": {
      "type": "http",
      "url": "https://docs.example.com:8765/mcp",
      "headers": { "Authorization": "Bearer ${ISH_MCP_TOKEN}" }
    }
  }
}
```

The server speaks plain HTTP. Put it behind a reverse proxy that holds the
certificate when the token crosses a network you do not trust.

`ish remote search` and `ish remote ask` read such a server. See
[Ask a server](usage.md#ask-a-server).

### Give an agent the tree of a server

`ish remote mcp` serves the tools that read a server, on stdio. With
`search_code` and `ask`, the server searches and returns chunks, and the language model of
this machine writes each answer, with the login of this machine. `--url`,
`ISH_REMOTE`, or `remote` in your user config file names the server, as for the
other commands of `ish remote`:

```json
{
  "mcpServers": {
    "docs": {
      "command": "ish",
      "args": ["remote", "mcp", "--url", "https://docs.example.com/mcp"],
      "env": { "ISH_MCP_TOKEN": "${ISH_MCP_TOKEN}" }
    }
  }
}
```

It also serves `list_chunks`, `index_status`, and `complete_filter` from
the server. `refresh_index` changes the server, so it stays with its
operator, and `ish remote mcp` does not list it.

The tools take the arguments of the local tools, except `path`: the
server searches its own tree, so a call with a `path` gets an error. Use
`under` to narrow a search. Each call brings its own query and filters, so
the line of `ish remote mcp` takes neither. `--limit`, `--format`, and
`--ask-turns` on the line set the default of each call. The reply of
`ask` names the host and the version of the server in `server`.
Choose the model on this machine, as for a local answer. See
[Answer questions](configuration.md#answer-questions).
