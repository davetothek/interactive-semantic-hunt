# Architecture

Hexagonal, ports and adapters. The dependency direction is strict:

```
interfaces → application → domain
                 ↓
               ports
                 ↑
              adapters
```

- `domain` must not import `application`, `adapters`, or `interfaces`.
- `application` may import `domain` and `application.ports`. It must not
  import a concrete adapter or an interface.
- `interfaces` may import `application` and `domain`.
- `adapters` implement ports. They may import `domain` and the port they
  implement.
- `src/ish/bootstrap.py` is the only module that imports both application
  code and a concrete adapter. Nothing outside it constructs an adapter.
- Python `ast` is used only in the Python parser adapter.
- Terminal and argument handling belong only in the CLI interface.
- `src/ish/__init__.py` imports no layer. `tests/unit/test_package.py` checks
  this in a fresh interpreter.
- Keep the domain model clean. `Chunk` holds no vector, no AST node, no
  parser internals, and no UI state.
- Do not add a feature outside the four interfaces. An HTTP API stays out.
- Do not swallow an error. Report a parse failure to stderr, or return a
  structured error.

Read `.claude/CLAUDE.md` for the map of the code and for the measurements
behind each rule. Read `src/` before a substantial change.
