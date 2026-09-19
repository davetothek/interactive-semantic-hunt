---
paths:
  - "src/ish/bootstrap.py"
  - "src/ish/settings.py"
  - "src/ish/interfaces/python/api.py"
---

# Composition and settings

## bootstrap.py

- This is the composition root. It selects from the two registries,
  `PARSERS` in `adapters/parser` and `EMBEDDERS` in `adapters/embedder`, and
  wires adapters into use cases. It does not hold a registry itself.
- `build_stores()` returns two things: the index a refresh may write, or
  `None` when a search reads several indexes and may write to none, and the
  reader a search consults. A federated reader never sees a write.
- `build_vocabulary()` reads aliases and categories off the registry once.
  `Ish` caches the result, because a picker narrows on every keystroke.
- `build_result_filter()` is the one place a query filter is joined to
  `type_patterns` and the language resolver.
- `refresh_indexes()` visits each stored tree in turn with federation off,
  and reads the configuration beside each tree, not beside the parent.

## settings.py

- Every option is a field on the frozen `Settings` dataclass. The CLI flags
  and the TOML keys derive from the fields. Add an option by adding a field.
  Never edit `args.py` for it.
- There is no config-only or CLI-only option. `tests/unit/test_settings.py`
  checks the parity in both directions.
- Precedence is resolved only in `load_settings()`:
  `defaults < ~/.config/ish/config.toml < ./.ish/config.toml (searched
  upward) < ISH_* env < CLI flags`.
- A config file beside a subtree adds to the one above it. Each file settles
  only the keys it names.
- An unknown key warns and is skipped. A malformed or unreadable file raises
  `ConfigError` and exits 1. A directory of the config file's name is absent.
- Every field carries a `scope`. Query scope (`lang`, `under`, `type`,
  `limit`, `no_hybrid`) may be set per call. Index scope may not. A per-call
  index option would make the next refresh prune what that call excluded.

## Use cases

- No use case receives a `Settings` object. `Scan`, `Index`, and `Search`
  take explicit constructor arguments. `bootstrap` reads the settings and
  passes values.

## Ish

- `Ish` is the session every interface shares. Anything every interface must
  do the same way goes there, not into each interface.
- `Ish` resolves the filter chain once: a filter typed into the query beats a
  call argument, which beats configuration.
