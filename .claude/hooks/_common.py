"""Read a hook's input and write its decision.

Every hook in this directory is a PreToolUse hook. It reads one JSON
object from stdin, decides, and answers in one of three ways:

- allow: exit 0 with nothing on stdout. The tool runs as sent.
- ask: exit 0 with a JSON decision on stdout. The user confirms first.
- deny: exit 2 with the reason on stderr. The tool does not run, and
  Claude reads the reason.

A hook that cannot read its input allows. A broken hook must never stop
the work it guards.

Standard library only. A hook runs on every matching tool call, so it
must not pay for an import or a virtual environment.
"""

import json
import sys
from dataclasses import dataclass
from typing import Any

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


@dataclass(frozen=True)
class Decision:
    """What a hook decided, and why."""

    kind: str
    reason: str = ""

    @property
    def blocks(self) -> bool:
        return self.kind == DENY


def allow() -> Decision:
    return Decision(ALLOW)


def ask(reason: str) -> Decision:
    return Decision(ASK, reason)


def deny(reason: str) -> Decision:
    return Decision(DENY, reason)


def read_input() -> dict[str, Any] | None:
    """Return the hook input, or None when stdin holds no JSON object."""
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def answer(decision: Decision, hook_name: str) -> int:
    """Write *decision* the way Claude Code reads it, and return the exit code."""
    if decision.kind == DENY:
        sys.stderr.write(f"{hook_name}: {decision.reason}\n")
        return 2
    if decision.kind == ASK:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"{hook_name}: {decision.reason}",
            }
        }
        sys.stdout.write(json.dumps(output) + "\n")
    return 0


def replace_input(tool_input: dict[str, Any]) -> int:
    """Write a replacement for the tool's input, and return the exit code.

    ``updatedInput`` replaces the whole input, so *tool_input* must hold
    every field the tool needs, changed or not.
    """
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": tool_input,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")
    return 0
