# Workflow

## Branches and commits

- Work on a branch from `origin/main`. Never commit on `main`. Never push to
  `main`. A hook blocks the push.
- Push only to the branch you were told to use.
- One issue per commit. Put `Closes #N` in the body.
- Do not discard uncommitted work. `git checkout -- <path>`, `git restore
  <path>`, `git reset --hard`, and `git stash drop` are blocked by a hook.
  Use `git stash` to set work aside, or commit it.
- Never force-push a branch you did not create.

## Before you say a task is done

- Run `/check`. It runs lint, the type checker, the test suite at 100 %
  coverage, and the hook tests. As root it runs the suite as an unprivileged
  user, because six permission tests pass for the wrong reason as root.
- A new line of code arrives with the test that reaches it.
- A fixed defect arrives with a test that names it.

## Pull requests

- Do not open a pull request unless the user asks. Use `/pr` when they do.
- Reference the issues the branch closes. Describe what was measured.

## Releases

- A version bump is its own pull request from a `release/X.Y.Z` or
  `hotfix/X.Y.Z` branch, merged with a merge commit. Never bundle it with
  other work. A hook blocks a bump on any other branch.
- Pushing a tag publishes to PyPI. Only the user pushes a tag. Use
  `/release`, which stops and prints the commands.

## Dependencies

- Ask before adding a dependency. `uv add` prompts for confirmation.
- Do not add a client package for Ollama. The standard library reaches it.

## Scope

- Do the task asked. Do not widen it, and do not narrow it without saying so.
- Accuracy beats speed. Before a change to ranking, prefixes, or chunking,
  run `/benchmark` and report the table.
- When a change is justified by a measurement, take the measurement with
  `/measure` and put both numbers in the commit body.
