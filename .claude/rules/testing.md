---
paths:
  - "tests/**"
---

# Testing

- Unit tests live in `tests/unit/` and mirror the source tree down to the
  subpackage: `tests/unit/adapters/parser/`, `tests/unit/interfaces/mcp/`.
- Integration tests live in `tests/integration/cli/`. At least one runs the
  CLI against a temporary project with nested directories.
- Coverage is 100 % and the run fails below it. A new line arrives with the
  test that reaches it. Do not add `# pragma: no cover` to reach the number.
- Use fakes when testing application orchestration. Do not couple an
  application test to a real parser or a real backend.
- A fake embedder declares `model_name`. The port requires it.
- Test a parser adapter against known source strings. For Python: functions,
  async functions, classes, methods, async methods, several definitions,
  qualified names, line numbers, source text, and a syntax error.
- When you fix a defect, add a test class or method that names it, so the
  next reader finds the reason. `TestRefreshSettles`,
  `TestMigrationErasesOldContent`, and `TestThreadSafety` are the pattern.
- Six tests turn a file or directory unreadable. They pass for the wrong
  reason as root. Run the suite as an unprivileged user, through `/check`.
- Test the TUI with Textual's pilot against a mounted DOM. Assert what ran,
  not how many times it ran. Debounce timing is not deterministic.
- Never skip, disable, or quarantine a test to get green.
- A test that sleeps is a test that flakes. Fake the clock or record the
  call.
