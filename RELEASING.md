# Releasing

The tag is the decision. Pushing `vX.Y.Z` builds, checks, and publishes
that version to PyPI; nothing else does.

## Once, before the first release

PyPI must be told to trust this workflow. Nothing is stored here, so
there is no token to leak or rotate.

1. Sign in to <https://pypi.org> and open **Your projects → Publishing**,
   or <https://pypi.org/manage/account/publishing/> for a name that does
   not exist yet.
2. Add a **pending publisher**:
   - PyPI project name: `interactive-semantic-hunt`
   - Owner: `davetothek`
   - Repository: `interactive-semantic-hunt`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. In GitHub, open **Settings → Environments** and add an environment
   named `pypi`. Require a reviewer if a release should need approval.

## Each release

`poe release` cuts one. It refuses a working tree with changes in it,
writes the version into `pyproject.toml`, brings `uv.lock` in step, runs
`poe check`, then commits and tags. A check that fails puts both files
back and commits nothing.

```sh
poe release 0.2.0   # the version a milestone names
poe release         # raise the patch number
```

It does not push. Pushing the tag is the decision to publish, and it is
the only thing that reaches PyPI:

```sh
git push origin main v0.2.0
```

Watch the run. The publish job waits on the build job, so a failing
check never reaches PyPI, and the workflow refuses a tag whose name
disagrees with the version it finds in `pyproject.toml`.

Nothing releases on its own. Closing a milestone says the work is done;
running `poe release` says to ship it.

## A fix that cannot wait for the milestone

A release cut from `main` carries everything merged since the last tag,
so a patch cut there would also ship whatever half-built milestone work
is sitting on `main`. That is not what a patch number promises.

Cut it from the release it fixes instead. The fix reaches `main` first,
through a pull request like any other, and the patch takes a copy:

```sh
git switch -c hotfix/0.1.2 v0.1.1
git cherry-pick <the fix, already on main>
poe release
git push origin hotfix/0.1.2 v0.1.2
git switch main && git merge hotfix/0.1.2
```

That patch holds the fix and nothing else. There is no standing branch
to keep in step for it, because the branch only has to exist for as long
as the fix does.

## What ships

The wheel carries the package and three commands: `ish`, `ish-mcp`, and
`ish-complete`. It depends on nothing that compiles: the default backend
reaches Ollama over HTTP with the standard library.

A backend that needs more is an extra, so a reader who wants one asks
for it:

```sh
pip install interactive-semantic-hunt          # Ollama, the default
pip install "interactive-semantic-hunt[llama]" # in-process llama.cpp
pip install "interactive-semantic-hunt[st]"    # sentence-transformers
```

The build job installs the wheel on its own and runs it, because a
package that cannot be installed alone is not releasable whatever the
tests say.
