"""What each call of a language model may do, and the trace a call leaves.

Bootstrap builds one ``CallOptions`` from the settings and gives it to
each backend. A backend uses the parts that apply to it.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ish.application.ports.language_model import LanguageModelError

# How long claude-cli keeps the prompt of a call in its cache. Each turn
# of an answer is a new call with a new message, so a write is rarely
# read after the call that made it. A write for 5 minutes costs 1.25
# times an uncached token, and a write for 1 hour 2 times.
PROMPT_CACHES = ("5m", "1h", "off")
DEFAULT_PROMPT_CACHE = "5m"

# What one call that reads the tree may cost, in US dollars, at the prices
# of the API. A model that reads in circles stops there.
DEFAULT_READ_USD = 1.0

# The effort a call of claude-cli may ask for. Empty asks for none, and
# claude then uses the default of the model.
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")

# The command that runs Claude Code.
DEFAULT_CLAUDE = "claude"

# Characters of chunk text one answer may send, over every turn.
DEFAULT_BUDGET_CHARS = 60_000


@dataclass(frozen=True, slots=True)
class CallOptions:
    """Hold the options of each call a backend makes.

    Attributes:
        prompt_cache: How long claude-cli keeps a prompt in its cache:
            ``5m``, ``1h``, or ``off``.
        read_usd: What one call that reads the tree may cost, in US dollars.
        trace: The directory that gets one JSON file for each call, or
            None for no trace.
        answer_effort: The effort of claude-cli for a turn of an answer.
        rerank_effort: The effort for a rerank and for the query of a chat.
        check_effort: The effort for a check of the claims.
        fallback_model: The model claude-cli asks when the model of the
            call is overloaded, or empty for none.
        claude: The command that runs Claude Code.
        timeout: Seconds a call may take, or 0 for the default of the
            backend.
        budget_chars: Characters of chunk text one answer may send. A
            backend with a window of fixed size makes it fit them.
        resume: Let each later turn of an answer of claude-cli that reads
            the tree resume the session of the turn before, and send only
            what is new.
        autocompact: The size in tokens at which claude summarizes a
            session it resumes, or 0 for the default of claude.
    """

    prompt_cache: str = DEFAULT_PROMPT_CACHE
    read_usd: float = DEFAULT_READ_USD
    trace: Path | None = None
    answer_effort: str = ""
    rerank_effort: str = ""
    check_effort: str = ""
    fallback_model: str = ""
    claude: str = DEFAULT_CLAUDE
    timeout: float = 0.0
    budget_chars: int = DEFAULT_BUDGET_CHARS
    resume: bool = False
    autocompact: int = 0

    def __post_init__(self) -> None:
        """Refuse a cache or an effort that claude-cli does not know."""
        if self.prompt_cache not in PROMPT_CACHES:
            raise LanguageModelError(
                f"prompt_cache {self.prompt_cache!r} is not one of "
                f"{', '.join(PROMPT_CACHES)}."
            )
        for name in ("answer_effort", "rerank_effort", "check_effort"):
            if getattr(self, name) not in EFFORTS:
                raise LanguageModelError(
                    f"{name} {getattr(self, name)!r} is not one of "
                    f"{', '.join(e for e in EFFORTS if e)}, or empty."
                )

    def effort_of(self, kind: str) -> str:
        """Return the effort of a call of *kind*, the method that makes it."""
        if kind == "check":
            return self.check_effort
        if kind in ("select", "query"):
            return self.rerank_effort
        return self.answer_effort


def write_trace(directory: Path, kind: str, record: dict[str, object]) -> Path:
    """Write *record* as the next file in *directory*, and return its path.

    Number the files in the order of the calls, so a reader can follow
    the data of an answer from the first call to the last.
    """
    directory.mkdir(parents=True, exist_ok=True)
    number = 1 + sum(1 for _ in directory.glob("*.json"))
    text = json.dumps(record, indent=1, ensure_ascii=False)
    while True:
        path = directory / f"{number:03d}-{kind}.json"
        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(text)
        except FileExistsError:
            number += 1
            continue
        return path
