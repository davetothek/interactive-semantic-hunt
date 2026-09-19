---
paths:
  - ".claude/**"
---

# Claude configuration

## Hooks

- A hook is a standard-library Python script under `.claude/hooks/`, run with
  the system `python3`. Keep it compatible with 3.11. Do not import from the
  package or the virtual environment.
- A hook that cannot read its input allows. A broken hook must never stop the
  work it guards.
- Each rule in a hook has a test row in `.claude/hooks/tests/`. A blocked
  command sits beside its nearest allowed neighbor. Run `poe hooks`.
- Keep each hook under 100 ms. `guard_git` runs on every Bash command.
- Claude Code snapshots hooks at session start. A change to
  `.claude/settings.json` or to a hook takes effect in the next session, or
  after review in the `/hooks` menu.
- `route_model` logs each decision to `<scratchpad>/model-routing.jsonl`.
  Read that log before changing a routing rule or threshold.

## Rules

- One topic per file. A rule states an obligation. The reason and the
  measurement stay in `CLAUDE.md`.
- A rule that applies to some files carries `paths:` frontmatter. A rule
  with no `paths:` loads every session, so keep it short.
- Do not write a sentence in a rule that also appears in `CLAUDE.md`. Two
  copies drift.

## Skills

- A skill is a procedure the model would otherwise improvise. It names the
  steps, the commands, and the artefact it produces.
- A skill with a side effect the user must control, such as `pr` and
  `release`, sets `disable-model-invocation: true`.
- Keep `SKILL.md` under 200 lines. Put a long reference in a file beside it.
