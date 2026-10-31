"""Every language model backend ish ships, and how to add one.

A backend writes an answer from the sources it is given. Write a class
that satisfies ``LanguageModel``, takes the ``answer_model`` option, and
sends the instructions it is given rather than its own:

    class MyModel:
        def __init__(self, model: str = "", *, prompts: Prompts | None = None):
            self.model_name = model or "default-model"
            self._prompts = prompts or load_prompts()

        def answer(self, question, sources) -> Reply:
            ...                              # send self._prompts.answer

The instructions live as text files in ``prompts/``, and ``prompt_dir``
replaces any of them. A backend with no citation feature replies in the
JSON that ``_grounded`` fixes, and parses it there.

To add a backend to ish:

1. Write the class in a new module in this package, beside the others.
2. Add one line to ``LANGUAGE_MODELS`` below, keyed by the
   ``--language-model`` name.

A backend receives the text of the documents. Bootstrap builds one only
when ``allowed_language_models`` names it, so a new entry here sends
nothing anywhere until an operator allows it.
"""

from typing import Protocol

from ish.adapters.language_model.anthropic import AnthropicLanguageModel
from ish.adapters.language_model.claude_cli import ClaudeCliLanguageModel
from ish.adapters.language_model.ollama import OllamaLanguageModel
from ish.adapters.language_model.prompts import Prompts
from ish.application.ports.language_model import LanguageModel


class LanguageModelFactory(Protocol):
    """What a registry entry is: a model option and instructions in, a backend out."""

    def __call__(self, model: str, *, prompts: Prompts) -> LanguageModel: ...


LANGUAGE_MODELS: dict[str, LanguageModelFactory] = {
    "anthropic": AnthropicLanguageModel,
    "ollama": OllamaLanguageModel,
    "claude-cli": ClaudeCliLanguageModel,
}
"""Language model backends by option name. Register a new backend here."""
