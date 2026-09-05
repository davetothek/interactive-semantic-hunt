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

## Every pull request

Write the change into `CHANGELOG.md`, under `## Unreleased`, in the pull
request that makes it. One line, for a reader of the tool rather than a
reviewer of the diff. A pull request title says what the work was; a
changelog line says what the release gives.

Label the pull request `indexing`, `speed`, `interfaces`, or `project`.
The release page groups the generated list under those headings, which
is what makes a missing changelog line visible.

Never change the version. A version bump is its own pull request, opened
once everything for the release already sits on `main`; it does not
travel with the work that motivated it. The `version` check refuses any
other pull request that touches it, so the rule is enforced rather than
remembered. A `release/*` or `hotfix/*` branch, or the `release` label,
says a pull request is the bump.

## Each release

`poe release` cuts one. It refuses a working tree with changes in it,
names the `## Unreleased` heading for the version and dates it, writes
the version into `pyproject.toml`, brings `uv.lock` in step, runs
`poe check`, then commits and tags. A check that fails puts all three
files back and commits nothing.

It also refuses a release with nothing under `## Unreleased`. A release
that says nothing is either empty or missing a line, and both need a
person to decide which.

```sh
poe release 0.2.0   # the version a milestone names
poe release         # raise the patch number
```

Naming the version `pyproject.toml` already declares tags that version
instead of raising it. A release prepared and never tagged is a release
still owed, and it takes the same checks as any other.

It does not push. Pushing the tag is the decision to publish, and it is
the only thing that reaches PyPI:

```sh
git push origin main v0.2.0
```

Watch the run. The publish job waits on the build job, so a failing
check never reaches PyPI, and the workflow refuses a tag whose name
disagrees with the version it finds in `pyproject.toml`.

The release job runs last and publishes the GitHub release page: the
same wheel and sdist PyPI received, and a body taken from this version's
`CHANGELOG.md` entry. Nothing is built a second time, so the files on
the release page are the files people install.

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
