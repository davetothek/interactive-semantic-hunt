---
name: add-embedder
description: >-
  Add an embedding backend to ish: one PrefixingEmbedder subclass under
  adapters/embedder, one EMBEDDERS entry, the optional extra and its type
  checker override, and tests that fake the library. Use it when the user
  asks to support a new embedding provider, model server, or backend.
argument-hint: "[backend-name]"
---

# Add the backend `$ARGUMENTS`

A backend is one module and one registry line. The port, `bootstrap`, and
every interface stay as they are.

When the user asks for a dry run, list the files you would create and
change, and stop.

## 1. Write the module

`src/ish/adapters/embedder/$ARGUMENTS.py`:

```python
"""<Provider> adapter for the Embedder port."""

from collections.abc import Sequence

from ish.adapters.embedder.prefixes import PrefixingEmbedder

DEFAULT_MODEL = "<model>"


class <Name>Embedder(PrefixingEmbedder):
    """Generate embeddings through <provider>."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        super().__init__(model_name)
        # Import the library here, never at module scope. The extra may be
        # absent, and the CLI names it when this raises.
        import <library>

        self._client = <library>...

    @classmethod
    def from_option(cls, model: str) -> "<Name>Embedder":
        """Build the backend the ``model`` option names. Empty means the default."""
        return cls(model) if model else cls()

    def _embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one vector per text, in order."""
        ...
```

Rules the module must keep:

- `_embed` receives texts that already carry their task prefix. Do not add
  one. Return exactly `len(texts)` vectors, in order.
- Raise `RuntimeError` with a sentence that says what to do next when the
  provider refuses or cannot be reached. Look at `ollama.py` for the
  wording.
- A query and a stored text may wait different lengths. Override
  `_embed_interactive` only when the provider can wait differently.
- Never write to stdout. The MCP interface owns it.

## 2. Register

In `src/ish/adapters/embedder/__init__.py`:

```python
"$ARGUMENTS": <Name>Embedder,
```

The `--embedder` choices derive from the keys.

## 3. Declare the extra

When the backend needs a package the default install does not carry, add an
extra under `[project.optional-dependencies]` in `pyproject.toml`, and add
the module path to the `include` list of the `[[tool.ty.overrides]]` entry
that ignores `unresolved-import`. Run `uv lock`. Ask before adding the
dependency: the hook prompts on `uv add`.

## 4. Prefixes

When the model was trained with a task prefix for documents and another for
queries, add a row to `_CONVENTIONS` in `prefixes.py`, keyed by model name.
A new prefix changes what a stored vector means, so bump `SCHEMA_VERSION` in
`adapters/vector_store/sqlite.py` and say so in the changelog.

## 5. Test

`tests/unit/adapters/embedder/test_$ARGUMENTS.py`. Fake the library with a
`monkeypatch` on `sys.modules` or on the client class, the way
`test_llama_cpp.py` does. Cover: the default model, the model option, one
vector per text in order, an empty input that calls nothing, the error a
refused request produces, and that the port is satisfied
(`isinstance(embedder, Embedder)`).

## 6. Finish

Add a line under `### Added` in `CHANGELOG.md`. Run `/check`. Commit. Do
not open a pull request.
