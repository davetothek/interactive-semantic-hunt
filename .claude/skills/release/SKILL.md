---
name: release
description: >-
  Cut a release of ish on its own release branch: set the version, promote
  the changelog, prove the build, commit, tag locally, push the branch, and
  stop before the tag push that publishes to PyPI. Only the user starts this.
disable-model-invocation: true
argument-hint: "[X.Y.Z]"
---

# Release `$ARGUMENTS`

A version bump is its own pull request, merged with a merge commit. It is
never bundled with other work. The tag push is the publish, and the user
runs it.

## 1. Refuse a dirty tree

```
git status --porcelain
```

Anything printed: stop. Say what is uncommitted. Do not stash it yourself.

## 2. Refuse an empty changelog

Read `CHANGELOG.md`. `## Unreleased` must hold at least one entry. When it
is empty, stop and list the commits since the last tag, so the user can say
what a reader should be told:

```
git log --oneline "$(git describe --tags --abbrev=0)"..HEAD
```

## 3. Branch

```
git fetch origin main
git switch -c release/$ARGUMENTS origin/main
```

For a fix that cannot wait for `main`, the branch is `hotfix/X.Y.Z` from the
previous tag, with the fix cherry-picked:

```
git switch -c hotfix/X.Y.Z vPREVIOUS
git cherry-pick <sha>
```

## 4. Cut

```
uv run poe release $ARGUMENTS
```

The script sets `version` in `pyproject.toml`, renames `## Unreleased` to the
version and today's date, relocks, runs `poe check`, commits `Release X.Y.Z`,
and tags `vX.Y.Z` locally. When any step fails it puts the files back and
nothing is committed. Read its message and stop.

## 5. Push the branch, not the tag

```
git push -u origin release/$ARGUMENTS
```

## 6. Stop, and hand over

Print this, filled in, and do nothing more:

```
Branch release/X.Y.Z is pushed. To finish:
  1. Open a pull request from release/X.Y.Z into main (/pr).
  2. Merge it with a merge commit, so vX.Y.Z is reachable from main.
  3. Publish:  git push origin vX.Y.Z
release.yml then builds, uploads to PyPI, and writes the GitHub release
from the changelog entry.
```

Never run step 3. The hook refuses it, and it is not yours to decide.
