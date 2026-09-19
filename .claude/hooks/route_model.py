"""Pick the cheapest model that can do a subtask well.

PreToolUse on Agent. When the caller names a model, nothing changes.
Otherwise one rule fires, in this order:

1. An Explore agent reads and never writes: haiku.
2. A Plan agent designs: opus.
3. A prompt that names a protected path or symbol: opus. These are the
   places where a wrong change costs more than the tokens saved.
4. A prompt that uses a design word: opus.
5. A prompt longer than LONG_PROMPT_CHARS: opus. A subtask that needs
   that much context is not a bounded change.
6. Anything else is a bounded change with a test to hit: sonnet.

The bias is toward opus. A false positive costs tokens. A false negative
costs a bad change in a place that matters.

Every decision is appended to ``model-routing.jsonl`` in the session's
scratchpad, so the rules can be tuned from what actually happened.
"""

import json
import re
import sys
import time
from pathlib import Path

from _common import read_input, replace_input

HOOK = "route_model"

HAIKU = "haiku"
SONNET = "sonnet"
OPUS = "opus"

LONG_PROMPT_CHARS = 4000

# Files and symbols where a change needs judgment rather than speed.
PROTECTED = (
    "bootstrap.py",
    "application/ports",
    "ranking.py",
    "vector_store/sqlite.py",
    "SCHEMA_VERSION",
    "SEMANTIC_WEIGHT",
    "is_code_like",
    "prefixes",
)

_DESIGN_WORD = re.compile(
    r"\b(design|spec|refactor|architecture|migrate|schema|why)\b",
    re.IGNORECASE,
)

READ_ONLY_AGENTS = frozenset({"Explore"})
DESIGN_AGENTS = frozenset({"Plan"})


def route(tool_input: dict) -> tuple[str, str] | None:
    """Return ``(model, rule)`` for *tool_input*, or None to leave it alone."""
    if tool_input.get("model"):
        return None

    agent = str(tool_input.get("subagent_type") or "")
    text = f"{tool_input.get('description', '')}\n{tool_input.get('prompt', '')}"

    if agent in READ_ONLY_AGENTS:
        return HAIKU, "read-only agent"
    if agent in DESIGN_AGENTS:
        return OPUS, "design agent"
    hit = next((name for name in PROTECTED if name in text), None)
    if hit is not None:
        return OPUS, f"protected: {hit}"
    word = _DESIGN_WORD.search(text)
    if word is not None:
        return OPUS, f"design word: {word.group(1).lower()}"
    if len(text) > LONG_PROMPT_CHARS:
        return OPUS, "long prompt"
    return SONNET, "bounded change"


def log_decision(scratchpad: str, tool_input: dict, model: str, rule: str) -> None:
    """Append one line of evidence. A failure to log stops nothing."""
    if not scratchpad:
        return
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model,
        "rule": rule,
        "subagent_type": tool_input.get("subagent_type"),
        "description": tool_input.get("description"),
        "prompt_chars": len(str(tool_input.get("prompt", ""))),
    }
    try:
        path = Path(scratchpad) / "model-routing.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as out:
            out.write(json.dumps(entry) + "\n")
    except OSError:
        return


def main() -> int:
    data = read_input()
    if data is None or data.get("tool_name") != "Agent":
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    chosen = route(tool_input)
    if chosen is None:
        return 0
    model, rule = chosen
    log_decision(str(data.get("scratchpad_dir") or ""), tool_input, model, rule)
    return replace_input({**tool_input, "model": model})


if __name__ == "__main__":
    sys.exit(main())
