# Extend

Two things are made to be added: a language and an embedding backend.
Each has one folder, one table, and a recipe at the top of the folder's
`__init__.py`.

**A language** is a class with a `language`, a set of `suffixes`, and a
`parse(path, source)` that returns chunks. Put it in a new module under
`src/ish/adapters/parser/` and add one line to `PARSERS` in that package's
`__init__.py`:

```python
PARSERS = {
    ...
    "toml": Language(TomlParser, aliases=frozenset({"tml"}), category="config"),
}
```

That line is the whole registration. It says how to build the parser, what
else a reader may call the language, and whether it holds code, prose, or
configuration. File discovery, the `--languages` and `--lang` choices, the
chunk size cap and the registry tests all follow from it. Nothing else needs
editing.

A parser that belongs to one project rather than to ish goes in
`~/.config/ish/parsers/` as a module with a `parser()` function. It joins the
same table at run time, may declare `aliases` and a `category` of its own, and
may replace a built-in language.

**A backend** subclasses `PrefixingEmbedder`, implements `_embed(texts)` and
a `from_option(model)` classmethod, and takes one line in `EMBEDDERS` in
`src/ish/adapters/embedder/__init__.py`. The `--embedder` choices derive from
the keys. A backend that needs a package the default install does not carry
is an extra in `pyproject.toml`, and a model trained with task prefixes gets
a row in `prefixes.py`.
