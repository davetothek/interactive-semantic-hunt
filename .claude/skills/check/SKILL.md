---
name: check
description: >-
  Run every check the way CI runs it: ruff lint, ty typecheck, the test suite
  at its 100 percent coverage gate, and the hook tests. As root it runs the
  suite as an unprivileged user, because the permission tests pass for the
  wrong reason as root. Use it before saying any change is done, and whenever
  the user asks to run the tests or checks.
allowed-tools: Bash(uv run *), Bash(bash .claude/skills/check/*), Bash(id *)
---

# Check

Run the four steps in this order and stop at the first failure. Show the
failing output as it is. Do not summarize a failure away.

```
uv run poe lint
uv run poe typecheck
```

Then the suite. Six tests make a file or a directory unreadable, and root can
read anything, so as root the suite must run as another user:

```
if [ "$(id -u)" = "0" ]; then
  bash .claude/skills/check/run_as_tester.sh
else
  uv run poe test
fi
```

Then the hooks:

```
uv run poe hooks
```

## When something fails

- A lint or format finding: run `uv run poe format`, then fix what remains by
  hand.
- A type finding: fix the code, not the checker. An optional extra that may
  be absent gets a `[[tool.ty.overrides]]` entry.
- Coverage under 100 %: the report names the lines. Write the test that
  reaches them. Never add `# pragma: no cover` to reach the number.
- A failing test: it is telling you something. Read it before changing
  anything. Never skip, disable, or loosen a test to get green.
- One of the six permission tests failing as a non-root user is a real
  failure.

## Report

One line per step: pass or fail, and for the suite the test count and the
coverage number.
