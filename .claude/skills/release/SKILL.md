---
name: release
description: >-
  Cut a release of ish from start to PyPI: set the version, promote the
  changelog, prove the build, commit, tag locally, open the release pull
  request, merge it when CI passes, push the tag that publishes, and confirm
  the upload. Only the user starts this.
disable-model-invocation: true
argument-hint: "[X.Y.Z]"
---

# Release `$ARGUMENTS`

A version bump is its own pull request, merged with a merge commit. It is
never bundled with other work. The tag push is the publish. The user starts
this skill, and the skill then runs to the end without a hand-over. It
stops at the first step that fails, and it reports what failed.

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

The script prints a push of the branch and the tag together. Do not run it.
The tag goes out in step 8, after the merge.

## 5. Push the branch, and open the pull request

```
git push -u origin release/$ARGUMENTS
gh pr create --base main --title "Release $ARGUMENTS" --body-file <file>
```

The body has the four sections of `/pr`. Why: what the release ships, in
one or two sentences. What: the version set and relocked, the changelog
section renamed, and the pull requests that hold the changes. Proof: the
`poe check` result that `poe release` printed. Checks: the tag exists
locally and is not pushed.

When `gh` answers "must be a collaborator", the active account cannot write
to the repository. Run the same `gh` command with the token of the account
that owns it: `GH_TOKEN="$(gh auth token -u OWNER)" gh ...`, where `OWNER`
is the owner in the `origin` URL.

## 6. Wait for CI

```
gh pr checks <N> --watch --fail-fast
```

A check fails: stop. Report the check and its log. Do not merge.

## 7. Merge with a merge commit

```
gh pr merge <N> --merge
```

A merge commit keeps the tagged commit reachable from `main`. Never squash
or rebase a release. Never pass `--admin`.

## 8. Publish

```
git fetch origin main
git push origin v$ARGUMENTS
```

Run the two as separate commands. `guard_git` judges a whole command line
before any of it runs, so a push chained after the fetch is judged against
the `origin/main` from before the fetch.

`guard_git` allows this one push. It checks that the tag commit is on
`origin/main` and that `pyproject.toml` there declares the version. A deny
names what is wrong: stop and report it. Never move, delete, or push again
a tag that is out.

## 9. Confirm the upload

```
gh run list --workflow release.yml --limit 1
gh run watch <run-id> --exit-status
curl -s https://pypi.org/pypi/interactive-semantic-hunt/json
```

The run must pass, and `info.version` on PyPI must read `$ARGUMENTS`. PyPI
can lag the run by a minute. A failed run after the tag is out: stop and
report the log. Do not retry by moving the tag.

## 10. Report

The pull request URL, the merge commit, the tag, the release run, and the
version PyPI shows.
