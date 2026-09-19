---
name: pr
description: >-
  Write a pull request for the current branch in this repository's house
  style — Why, What, Proof, Checks — show it to the user, and open it only
  after they confirm. Only the user starts this.
disable-model-invocation: true
---

# Pull request

## 1. Gather

```
git fetch origin main
git log --format='%h %s%n%b' origin/main..HEAD
git diff --stat origin/main..HEAD
```

Collect every `Closes #N` from the commit bodies. Read the check result of
the branch head when CI has run.

## 2. Write the body

Four sections, in this order. Prose in the house style: active voice, short
sentences, no marketing words.

```
## Why

What was wrong or missing, in the user's terms. Say what it cost when it was
a defect: the number of lines logged, the hours waited, the results lost.

## What

What changed, as a list. One item per commit or per decision. Name the file
when the reader would look for it.

## Proof, not assertion

A table or a list of what was checked and what it showed. A measurement
before and after. A command run and what it printed. Not "should work".

## Checks

Test count, coverage, lint and type check state. `poe hooks` when hooks
changed.

Closes #N
Closes #M
```

Title: the subject of the main commit, or one imperative sentence under 70
characters.

## 3. Show it

Print the title and body. Ask the user to confirm, change, or stop. Do not
open the pull request before they answer.

## 4. Open it

On confirmation, open a pull request from the current branch into `main`
with the GitHub tool available: `create_pull_request`, or `gh pr create
--base main --title ... --body-file ...`. Never mark it ready to merge, and
never merge it.

Print the URL. Offer, in one line, to watch the pull request for review
comments and CI failures.
