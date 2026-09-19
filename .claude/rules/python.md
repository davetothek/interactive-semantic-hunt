---
paths:
  - "**/*.py"
---

# Python

- Python 3.12 is the floor. The suite runs on 3.12, 3.13, and 3.14.
- Import `Sequence`, `Mapping`, `Iterable`, and `Callable` from
  `collections.abc`, never from `typing`.
- Use `typing` only for `Protocol`, `runtime_checkable`, `TypeAlias`,
  `TypeVar`, `ClassVar`, and other typing-only names.
- Do not write `from __future__ import annotations`. Quote an annotation that
  names the class being defined: `-> "Filters"`.
- `ruff` formats and lints. Line length 88, double quotes, space indent. Run
  `poe format` before `poe lint`.
- `ty` checks types. An optional extra that may be absent gets a
  `[[tool.ty.overrides]]` entry, not a bare `# type: ignore`.
- Import a heavy library where it is used, not at module scope, when the
  module is on the path of a CLI query. Interpreter start is most of a
  query's cost.
- Comments and docstrings follow `writing.md`, strict mode.
- Hooks under `.claude/hooks/` are the one exception to the floor. They run
  under the system `python3`, so they stay compatible with 3.11 and use the
  standard library only.
