---
paths:
  - "src/ish/adapters/embedder/**"
---

# Embedders

## Adding a backend

1. Write a subclass of `PrefixingEmbedder` in a new module in this package.
   Implement `_embed(texts)`, one vector per text in order, and
   `from_option(model)`, which reads the `model` option the way this backend
   needs. Empty means the default.
2. Add one `EMBEDDERS` entry in `__init__.py`, keyed by the `--embedder`
   name.

Then, when needed: an extra in `pyproject.toml` and a `[[tool.ty.overrides]]`
entry for its import, and a row in `prefixes.py` when the model was trained
with task prefixes. The `add-embedder` skill walks through it.

## Rules

- Import the backend's library inside `__init__`, never at module scope. An
  extra may be absent, and the CLI turns the `ModuleNotFoundError` into a
  line that names the extra.
- Do not add a client package for Ollama. Reach it with `urllib`. Importing
  the `ollama` package cost 71 % of a warm query.
- Task prefixes are keyed by model name in `prefixes.py`, because the
  convention belongs to the model, not to the backend serving it.
- Anything that changes what a stored vector means, the prefixes or the text
  handed to `_embed`, bumps `SCHEMA_VERSION` in the SQLite adapter. A
  change to how many texts a request carries does not, because each text is
  embedded on its own.
- `embed_documents` and `embed_query` stay separate. A stored text and a
  query take different prefixes.
- A query waits `QUERY_TIMEOUT_SECONDS`. An index run waits
  `TIMEOUT_SECONDS`. Somebody is watching the first.
- Send a batch again only when the daemon did not answer: a timeout, or an
  unreachable host. A refused request is not sent again. A query is sent
  once.
- Keep `DEFAULT_BATCH_SIZE` small. It sets how long a search waits behind an
  index run, and it carries no accuracy cost.
