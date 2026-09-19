---
name: work-issue
description: >-
  Close one GitHub issue in this repository the way the repository expects:
  read the issue, branch from main, pin the defect with a test, make the
  smallest change, add the changelog line, run every check, and commit with
  Closes #N. Use it whenever the user names an issue number to work on, or
  says "fix #N", "do #N", "take #N". It never opens a pull request.
argument-hint: "[issue-number]"
---

# Work one issue

Issue: `$ARGUMENTS`

## 1. Read the issue

Read the issue with the GitHub tool available (`issue_read`, or `gh issue
view N`). Note four things: the title, the **Suggested fix** section when
there is one, the labels, and whether it has sub-issues or a parent. When it
has sub-issues, work one sub-issue at a time and say which.

Stop and ask the user before writing code when the issue needs a decision:
it touches `bootstrap.py`, a port under `application/ports/`, `ranking.py`,
the SQLite schema, or a `SCHEMA_VERSION` bump, or the suggested fix names two
ways to do it. A bounded change with a clear fix needs no question.

## 2. Stand on the right branch

```
git status --porcelain          # must be empty, or stash first
git branch --show-current
```

On `main`: `git fetch origin main && git switch -c claude/<short-slug>
origin/main`. On a branch the user told you to use: stay on it. Never commit
on `main`.

## 3. Find the code

Use `Grep` and `Glob` over `src/ish/`. The map in `.claude/CLAUDE.md` names
the file for each concern. When a rule file loads as you open a file, read
it: it says what this part of the code must keep doing.

## 4. Pin it, then fix it

For a defect, write the failing test first, in the mirror of the source path
under `tests/unit/`. Name the test class or method after the defect, so the
next reader finds the reason (`TestRefreshSettles` is the pattern). Run it
and watch it fail.

Then make the smallest change that passes it. Do not widen the issue. If
you find a second problem, note it for the report and leave it.

## 5. Write the changelog line

When a user of the tool would notice the change, add one line under
`## Unreleased` in `CHANGELOG.md`, under `### Fixed`, `### Changed`, or
`### Added`. Say what a user sees, not what the code does.

## 6. Check

Run `/check`. Everything must be green: lint, types, the suite at 100 %
coverage, the hook tests. Never skip or weaken a test to get there.

## 7. Commit and push

One commit for the issue. Subject: one imperative sentence under 72
characters. Body: what happened, what it cost, what changed, and a
measurement when there is one. Last line: `Closes #N`.

```
git add <the files you changed>
git commit -F - <<'MSG'
<subject>

<body>

Closes #N
MSG
git push -u origin <branch>
```

Do not open a pull request. The user opens one, or asks for `/pr`.

## 8. Report

Say in a few lines: what changed and where, the test that pins it, the
check result, and anything you noticed and left alone.
