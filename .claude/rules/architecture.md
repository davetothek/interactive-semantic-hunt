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
- Do not add a feature outside the four interfaces. MCP over HTTP is the one
  network interface. A REST API stays out.
- Do not swallow an error. Report a parse failure to stderr, or return a
  structured error.

## Adapter layout

- One package per port under `src/ish/adapters/`.
- A plain module name is one backend: a language, an embedder, a language
  model, a store. One backend, one module.
- An underscore prefix is machinery. Its package uses it, and
  `bootstrap.py` may reach it as the composition root.
- A backend is one module while it fits in one file. When it needs a second
  file, it becomes a subpackage of the same name, such as `parser/asciidoc/`.
  Its `__init__.py` exports the backend, so the registry entry and every
  import stay the same. Inside it, the same naming holds.
- Machinery that several backends of one package share stays at the package
  level, with an underscore: `parser/_sections.py`.
- A language that only configures a shared engine is a factory,
  `<language>_parser()`. A language with behavior of its own is a class.
- `tests/unit/adapters/test_layout.py` checks the packages with a registry.

Read `.claude/CLAUDE.md` for the map of the code and for the measurements
behind each rule. Read `src/` before a substantial change.
