# Claude configuration for ish — Spec

## Overview

Give a model less capable than the one that wrote this repository a clear path
through it: a hook that picks the cheapest model able to do each subtask, eight
skills that package the workflows the repository repeats, and a rules directory
that turns the obligations now buried in a 479-line `CLAUDE.md` into short,
scoped, loadable rules. Six actions that must never happen become hooks that
block, because a rule is context and a hook is enforcement.

## Users & goals

- **Users:** the repository owner, running Opus as the session model on the
  0.2.0 milestone and beyond, and the subagents Opus spawns.
- **Primary job:** close issues correctly with the fewest tokens, without the
  owner re-explaining the architecture, the release process, or the writing
  style each session.
- **Success for the user:** a fresh session answers "how do I add a language"
  from a rule without opening the source, a subtask that only reads files never
  runs on Opus, and the six irreversible mistakes cannot happen.

## User stories

- As the owner, I want a subagent that searches or summarizes to run on Haiku
  and a subagent that changes `bootstrap.py` to run on Opus, so tokens go where
  judgment is needed.
- As the owner, I want to override the router by passing `model` myself, so
  the hook is a default and not a cage.
- As the owner, I want `/work-issue 64` to produce a branch, a commit that
  closes the issue, and a passing check run, without a PR I did not ask for.
- As the owner, I want `/release 0.3.0` to refuse to bundle a version bump with
  other work, and to stop before the tag push that publishes.
- As a session model, I want the parser rules to appear when I open a file
  under `adapters/parser/`, and not at every other moment.
- As a session model, I want to write a comment or a log string and have the
  strict writing rules already in context.
- As the owner, I want a broken router or guard to fail CI like any other code.

## Scope

**In scope (v1):**

- `.claude/settings.json` registering the hooks.
- `.claude/hooks/route_model.py` — `PreToolUse` on `Agent`.
- `.claude/hooks/guard_git.py` — `PreToolUse` on `Bash`.
- `.claude/hooks/guard_version.py` — `PreToolUse` on `Edit|Write`.
- `.claude/hooks/tests/` with a `poe hooks` target, in `poe check` and CI.
- `.claude/rules/` — twelve rule files, listed under **Rules layout**.
- `.claude/skills/` — eight skills: `work-issue`, `release`, `add-language`,
  `add-embedder`, `check`, `benchmark`, `pr`, `measure`.
- `CLAUDE.md` restructured to the architecture map and the measured evidence,
  with every obligation moved to a rule.

**Non-goals:**

- Custom agent definitions under `.claude/agents/`. The router works on the
  built-in agent types and the prompt. Agents are a follow-up if routing by
  prompt proves too coarse.
- A model call inside any hook. Routing is rules only.
- Changing what the tests, the linter, or the type checker enforce on `src/`.
- Any change to `src/ish/`. This work is configuration and documentation.
- Enforcing layer imports with a hook. `tests/unit/test_package.py` and the
  architecture tests already do.

## Acceptance criteria

### Router

- WHEN the `Agent` tool is called with `model` set THEN the router SHALL exit 0
  with no output, and the call SHALL run unchanged.
- WHEN `model` is absent and `subagent_type` is `Explore` THEN the router SHALL
  set `model` to `haiku`.
- WHEN `model` is absent and the prompt names a protected path or symbol
  (`bootstrap.py`, `application/ports/`, `ranking.py`, `vector_store/sqlite.py`,
  `SCHEMA_VERSION`, `SEMANTIC_WEIGHT`, `is_code_like`, `prefixes`) THEN the
  router SHALL set `model` to `opus`.
- WHEN `model` is absent and the prompt contains a design word (`design`,
  `spec`, `refactor`, `architecture`, `migrate`, `schema`, `why`) or
  `subagent_type` is `Plan` THEN the router SHALL set `model` to `opus`.
- WHEN `model` is absent and the prompt exceeds `LONG_PROMPT_CHARS` THEN the
  router SHALL set `model` to `opus`.
- WHEN `model` is absent and no rule above matches THEN the router SHALL set
  `model` to `sonnet`.
- WHEN the router sets a model THEN it SHALL return `updatedInput` holding
  every original field plus `model`, because `updatedInput` replaces the whole
  input.
- WHEN the router sets a model THEN it SHALL append one JSON line to
  `<scratchpad_dir>/model-routing.jsonl` naming the rule that fired, so the
  policy can be tuned from evidence.
- WHEN the router cannot parse its stdin THEN it SHALL exit 0 with no output.
  A broken router must never stop a subtask.

### Guards

- WHEN a `Bash` command pushes to `main` or `master` THEN `guard_git` SHALL
  exit 2 and name the rule.
- WHEN a `Bash` command pushes a tag, or runs `git push --tags`, THEN
  `guard_git` SHALL exit 2 and say the push publishes to PyPI.
- WHEN a `Bash` command runs `git push --force` THEN `guard_git` SHALL exit 2.
- WHEN a `Bash` command runs `git push --force-with-lease` THEN `guard_git`
  SHALL return `permissionDecision: ask`.
- WHEN a `Bash` command runs `git checkout -- <path>`, `git checkout <ref> --
  <path>`, `git checkout .`, `git restore <path>` without `--staged`,
  `git reset --hard`, `git stash drop`, `git stash clear`, or `git clean -f`
  THEN `guard_git` SHALL exit 2 and say the command discards uncommitted
  work.
- WHEN a `Bash` command runs `uv add`, `uv remove`, or `pip install` THEN
  `guard_git` SHALL return `permissionDecision: ask` with the reason "ask
  before adding a dependency".
- WHEN a `Bash` command is `git push origin <feature-branch>`, `git checkout
  <branch>`, `git restore --staged <path>`, or `git stash` THEN `guard_git`
  SHALL exit 0 with no output.
- WHEN an `Edit` or `Write` on `pyproject.toml` changes the `version =` line
  and the current branch does not match `release/*` or `hotfix/*` THEN
  `guard_version` SHALL exit 2 and name the release procedure.
- WHEN an `Edit` or `Write` on `pyproject.toml` leaves `version =` unchanged
  THEN `guard_version` SHALL exit 0.
- WHEN a guard cannot parse its stdin THEN it SHALL exit 0. A guard that
  cannot read the command has nothing to judge.

### Rules

- WHEN a session starts THEN the unscoped rules SHALL load: `architecture.md`,
  `writing.md`, `workflow.md`.
- WHEN a model reads a file under `src/ish/adapters/parser/` THEN
  `parsers.md` SHALL load, and the equivalent for each scoped rule.
- WHEN every rule has moved THEN `CLAUDE.md` SHALL be under 250 lines and SHALL
  hold no sentence that also appears in a rule.

### Skills

- WHEN `/pr` or `/release` is not invoked by the user THEN the model SHALL be
  unable to invoke it (`disable-model-invocation: true`).
- WHEN `/work-issue N` runs THEN it SHALL read issue N, branch from `origin/main`
  if not already on a work branch, implement, run `/check`, and commit with
  `Closes #N` in the body, and SHALL NOT open a PR.
- WHEN `/release X.Y.Z` runs THEN it SHALL refuse if the working tree has
  changes, create `release/X.Y.Z`, bump the version and the changelog, run
  `/check`, and stop with the exact commands the user must run for the tag.
- WHEN `/check` runs as root THEN it SHALL run the suite as an unprivileged
  user, because six permission tests pass vacuously as root.
- WHEN `/benchmark` runs THEN it SHALL produce the table under **Ranking** in
  `CLAUDE.md` (top-1 and MRR for conceptual, identifier, combined) for the
  current tree from `queries.toml`, and SHALL name the baseline it compares
  against. WHEN no backend answers THEN it SHALL exit 1 with the backend's
  own message and SHALL NOT print a table.
- WHEN `/measure` runs THEN it SHALL produce a before/after line in the form
  the evidence bullets use: what was measured, on what corpus, both numbers.

### Edge cases & failure behavior

- Router receives a prompt naming a protected path inside a quoted issue body
  → still `opus`. A false positive costs tokens; a false negative costs a bad
  change. Bias toward Opus.
- Router receives `subagent_type: Explore` and a prompt with a design word →
  `haiku`. The agent type is read-only by construction and wins.
- Guard receives a compound command (`cd x && git push origin main`) → the
  guard SHALL inspect every `&&`, `;`, and `|`-separated segment.
- Guard receives `git push origin HEAD:main` → blocked. The refspec names
  `main`.
- Guard receives `git push origin v0.2.0` where the token is not a known
  branch → treat any token matching `v[0-9]` or listed by `git tag` as a tag.
- `guard_version` cannot determine the branch (detached HEAD) → exit 2. An
  unknown branch is not a release branch.
- `python3` missing from the hook environment → the hook fails non-blocking
  and the transcript shows the notice. Document this in `claude-config.md`.

## Constraints

- **Stack / platform:** Python 3.12+, standard library only in hooks, so a hook
  runs with `python3` and pays no `uv` startup. Tests with `pytest`.
- **Dependencies / integrations:** Claude Code hooks (`PreToolUse` with
  `updatedInput`, `permissionDecision`), `.claude/rules/` with `paths`
  frontmatter, skills frontmatter (`disable-model-invocation`,
  `argument-hint`, `allowed-tools`). All verified against the documentation on
  2026-09-19.
- **Must not change:** anything under `src/ish/` and `tests/`; the 100 %
  coverage gate and what it measures; the release workflow in
  `.github/workflows/release.yml`; the meaning of any existing `poe` target.
  The `ste-writing` and `spec-interview` skills stay, with `ste-writing`
  trimmed to the rewrite procedure once strict mode lives in a rule.
- **Non-functional:** each hook must finish in under 100 ms on this machine
  (measured with `time`), because `PreToolUse` on `Bash` runs on every
  command. No network access from any hook.

## Rules layout

| file | scope (`paths`) | holds |
|---|---|---|
| `architecture.md` | always | dependency direction, the five layer rules, `ast` and terminal-handling placement |
| `writing.md` | always | STE strict mode for comments, log and error strings, commit bodies; the six comment rules |
| `workflow.md` | always | branch from `main`, one commit per issue, `Closes #N`, no PR unasked, run `/check` before done, ask before a dependency, never push to `main`, the release procedure in one paragraph |
| `composition.md` | `src/ish/bootstrap.py`, `src/ish/settings.py` | composition root is the only adapter importer, no `Settings` into a use case, add an option by adding a field, query vs index scope |
| `parsers.md` | `src/ish/adapters/parser/**` | one module + one `PARSERS` line, `SizeLimited` is automatic, `language` and `suffixes` on the class, aliases and category on the entry, plugin directory, heavy imports inside methods |
| `embedders.md` | `src/ish/adapters/embedder/**` | subclass `PrefixingEmbedder`, library import inside `__init__`, `from_option`, prefixes keyed by model, `SCHEMA_VERSION` bump when a vector's meaning changes, no `ollama` client package |
| `indexing.md` | `src/ish/adapters/vector_store/**`, `src/ish/application/index.py`, `src/ish/application/scan.py` | prune on a positive test only, opening writes nothing, stamped vs unstamped files, vacuum on schema change, every index rule in `Scan.accepts()`, thread safety |
| `ranking.md` | `src/ish/application/ranking.py`, `src/ish/adapters/vector_store/**` | policy lives once, filter inside the primitives, keep the `is_code_like` gate, run `/benchmark` before touching `SEMANTIC_WEIGHT` |
| `interfaces.md` | `src/ish/interfaces/**` | everything shared goes in `Ish`, MCP stdout is the transport, query-scope only per call, TUI focus and thread rules, daemon threads |
| `testing.md` | `tests/**` | mirror the source tree, fakes not real parsers, 100 % with the test that reaches each line, run as an unprivileged user, Textual pilot for the TUI |
| `claude-config.md` | `.claude/**` | how to change a hook (edit, add a test row, run `poe hooks`), that hooks are stdlib only, where routing decisions are logged |
| `python.md` | `**/*.py` | the 3.12 floor, `collections.abc` imports, no `__future__` annotations, lazy heavy imports on the query path, hooks as the 3.11 exception |

## Boundaries

- **Always:** run `poe check` and `poe hooks` before declaring any task done;
  keep every hook standard-library only; write prose in STE; one issue per
  commit with `Closes #N`.
- **Ask first:** adding a dependency; any change to `src/ish/` while doing this
  work; force-pushing; changing a routing threshold after the log shows a
  pattern.
- **Never:** push to `main`; push a tag; discard uncommitted work with
  `checkout --`, `restore`, `reset --hard`, or `stash drop`; call a model from
  a hook; open a PR without being asked.

## Success metrics & definition of done

- `poe hooks` runs a table-driven test with one row per routing rule and one
  for the override, and one pair per guard rule: the blocked command and its
  nearest allowed neighbour. All green, in `poe check`, in CI on 3.12–3.14.
- Each hook measured under 100 ms.
- Each skill invoked once against this repository with the expected artefact
  produced: `/work-issue` a branch and commit, `/pr` a PR body in the house
  style printed and not posted, `/release` a refusal on a dirty tree,
  `/check` a green run as `tester`, `/benchmark` the ranking table,
  `/measure` a before/after line, `/add-language` and `/add-embedder` a
  dry-run listing the files they would touch.
- `CLAUDE.md` under 250 lines. Every moved rule findable by `grep` in
  `.claude/rules/`. No sentence duplicated between the two.
- A fresh session, asked "how do I add a language", answers from `parsers.md`
  without reading `src/`.
- After one day of Opus on the milestone, the routing log shows Haiku and
  Sonnet took a majority of subtasks and no Sonnet subtask touched a protected
  path.

## Verified while building

- Each hook costs about 44 ms including interpreter start: 100 runs of
  `guard_git` in 4.4 s. All three run under the system Python 3.11.
- `/check` ran green through `run_as_tester.sh`: 1011 tests at 100 %
  coverage as the unprivileged user, lint and types clean, 95 hook tests.
- `benchmark.py` against an unreachable backend exits 1 with the Ollama
  adapter's message and no table. The retry from #60 fired twice on the way,
  which is the first live sighting of that fix.
- `CLAUDE.md` is 246 lines and shares no sentence over 40 characters with any
  rule file, checked by script.
- Every path in `queries.toml` names a file that exists.

## Open questions / assumptions

- **Assumption, still open:** `updatedInput` under `hookSpecificOutput` is
  honored without a `permissionDecision` beside it. The doc lists the fields
  as independent. Hooks load at session start, so this session could not test
  it. Verify in the first session that loads the config: spawn an `Explore`
  agent with no `model` and read `model-routing.jsonl`. Add
  `permissionDecision: allow` to `replace_input()` in `_common.py` if not.
- **Assumption:** the `Agent` tool's `tool_input` carries `description`,
  `prompt`, `subagent_type`, `model`, `run_in_background`, `isolation`, as its
  schema in this session shows. The router echoes whatever fields arrive, so a
  new field passes through.
- **Assumption:** `LONG_PROMPT_CHARS = 4000` is the first threshold. Tune from
  `model-routing.jsonl`.
- **Assumption:** the protected-path and design-word lists are a starting
  point. Bias is toward Opus, so the cost of a wrong list is tokens, not
  correctness.
- **Assumption:** `haiku` is a good fit for `Explore` here. If Explore results
  come back thin, the first change is `Explore → sonnet`, one line.
- **Open:** the benchmark numbers themselves. `queries.toml` holds 20 queries
  written for this repository, unverified against a live backend. The first
  run with Ollama is the baseline. A query that misses is not removed; it is
  either a ranking defect or a query that needs a better expected chunk, and
  the miss list says which.
- **Open:** whether a `SubagentStart` hook should inject the three unscoped
  rules into every subagent. Subagents load `CLAUDE.md` and rules already
  unless `omitClaudeMd` is set, so v1 does not add one.
