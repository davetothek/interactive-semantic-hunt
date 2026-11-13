"""The instructions a language model receives, read from text files.

Each instruction is one file in the ``prompts`` directory beside this
module: ``answer.md`` for an answer in JSON, ``answer_cited.md`` for an
answer with native citations, ``step.md`` for a turn that may ask for
searches, ``select.md`` for a rerank, ``query.md`` for the search query
of a question in a chat, and ``brief.md``, which a brief
answer adds to the instruction that writes it. A directory that an operator
names holds files of the same names, and each one it holds replaces the
default. A file it does not hold keeps the default.

A file is plain text. The lines of a paragraph join into one line, and a
blank line keeps paragraphs apart, so a file wraps at any width and still
sends the same words. ``$name`` is a value the code fills in, so that a
number in an instruction is the number the code enforces. ``$$`` is a
dollar sign.

The JSON schemas a reply must follow stay in code. They are a contract
with the parser, and an instruction that changes one must change both.

Notes add to the instructions and replace none: the notes of an answer
come after each instruction that writes one, and the notes of a rerank
after ``select.md``, each under a heading that says so. A note is sent as
written, with no value filled in.
"""

import logging
import string
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path

from ish.adapters.language_model._grounded import MAX_GAPS
from ish.application.ports.language_model import SEARCHES_A_TURN, LanguageModelError

log = logging.getLogger(__name__)

NAMES = ("answer", "answer_cited", "step", "select", "query", "brief")

# The reply word a backend with native citations gives when the
# documents do not answer. It cannot reply in JSON beside its citations.
NOT_COVERED = "NOT_COVERED"

# The heading above the notes, which tells the model what they are.
NOTES_HEADING = (
    "Notes for this tree. They add to the instructions above and change none of them:"
)

_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight")


@dataclass(frozen=True, slots=True)
class Prompts:
    """Hold the instruction for each kind of call a backend makes."""

    answer: str
    answer_cited: str
    step: str
    select: str
    query: str = ""
    brief: str = ""

    def briefly(self) -> "Prompts":
        """Return these instructions with ``brief.md`` after each that answers.

        A brief answer is the same call told to keep to its short
        answer, so the model writes no section it would only drop.
        """
        return replace(
            self,
            answer=f"{self.answer}\n\n{self.brief}",
            answer_cited=f"{self.answer_cited}\n\n{self.brief}",
            step=f"{self.step}\n\n{self.brief}",
        )


def load(
    directory: Path | None = None,
    *,
    answer_notes: str = "",
    rerank_notes: str = "",
    **values: int | str,
) -> Prompts:
    """Read the instructions, from *directory* where it holds a file.

    Fill each ``$name`` from *values*, a number as a word, and
    ``$answer`` in ``step.md`` with the answer instruction. Add
    *answer_notes* after each instruction that writes an answer, and
    *rerank_notes* after the rerank instruction. Raise
    ``LanguageModelError`` that names the file for a value no code
    fills, or for a directory that does not exist.
    """
    if directory is not None and not directory.is_dir():
        raise LanguageModelError(f"prompt_dir {directory} is not a directory.")
    filled = {
        "max_gaps": MAX_GAPS,
        "not_covered": NOT_COVERED,
        "searches_a_turn": SEARCHES_A_TURN,
        **values,
    }
    words = {k: _word(v) for k, v in filled.items()}
    texts = {
        name: _fill(name, _read(name, directory), words)
        for name in NAMES
        if name != "step"
    }
    for name, notes in (
        ("answer", answer_notes),
        ("answer_cited", answer_notes),
        ("select", rerank_notes),
    ):
        texts[name] = _noted(texts[name], notes)
    # Fill the step once, with the answer as one more value: a value is
    # never read again, so a dollar sign in the answer stays as written.
    texts["step"] = _fill(
        "step", _read("step", directory), {**words, "answer": texts["answer"]}
    )
    if directory is not None:
        extra = {p.stem for p in directory.glob("*.md")} - set(NAMES)
        for stem in sorted(extra):
            log.warning(
                "%s/%s.md names no instruction. Known: %s",
                directory,
                stem,
                ", ".join(NAMES),
            )
    return Prompts(**texts)


def _noted(text: str, notes: str) -> str:
    """Return *text* with *notes* after it, under their heading, when there are any."""
    notes = notes.strip()
    return f"{text}\n\n{NOTES_HEADING}\n\n{notes}" if notes else text


def _read(name: str, directory: Path | None) -> str:
    """Return the text of *name*, from *directory* when it holds the file."""
    if directory is not None:
        own = directory / f"{name}.md"
        if own.is_file():
            log.info("Read the %s instruction from %s", name, own)
            return _paragraphs(own.read_text(encoding="utf-8"))
    shipped = resources.files("ish.adapters.language_model") / "prompts" / f"{name}.md"
    return _paragraphs(shipped.read_text(encoding="utf-8"))


def _paragraphs(text: str) -> str:
    """Join the lines of each paragraph, and keep a blank line between them."""
    blocks = [" ".join(b.split()) for b in text.split("\n\n")]
    return "\n\n".join(b for b in blocks if b)


def _fill(name: str, text: str, values: Mapping[str, str]) -> str:
    """Fill the values a file names."""
    try:
        return string.Template(text).substitute(values)
    except (KeyError, ValueError) as exc:
        raise LanguageModelError(
            f"The {name} instruction uses {exc}, which no code fills. "
            f"Known values: {', '.join(sorted(values))}. Write $$ for a dollar sign."
        ) from exc


def _word(value: int | str) -> str:
    """Write a small count as a word, as the instructions read."""
    if isinstance(value, int) and 0 <= value < len(_WORDS):
        return _WORDS[value]
    return str(value)
