"""Every language model backend ish ships, and how to add one.

A backend writes an answer from the sources it is given. Write a class
that satisfies ``LanguageModel`` and takes the ``answer_model`` option:

    class MyModel:
        def __init__(self, model: str = "") -> None:
            self.model_name = model or "default-model"

        def answer(self, question, sources) -> Reply:
            ...                              # cite sources by their id

To add a backend to ish:

1. Write the class in a new module in this package, beside the others.
2. Add one line to ``LANGUAGE_MODELS`` below, keyed by the
   ``--language-model`` name.

A backend receives the text of the documents. Bootstrap builds one only
when ``allowed_language_models`` names it, so a new entry here sends
nothing anywhere until an operator allows it.
"""

from collections.abc import Callable

from ish.adapters.language_model.anthropic import AnthropicLanguageModel
from ish.application.ports.language_model import LanguageModel

LANGUAGE_MODELS: dict[str, Callable[[str], LanguageModel]] = {
    "anthropic": AnthropicLanguageModel,
}
"""Language model backends by option name. Register a new backend here."""
