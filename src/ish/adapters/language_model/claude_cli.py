"""Answer from sources with the Claude Code CLI, on the account it is logged in to.

The CLI has no citation feature, so the model replies in the JSON that
``_grounded`` fixes, through ``--json-schema``. It suits a person who
asks on their own machine. A shared server answers for many people, and
one login would carry them all, so a server uses the anthropic backend.

The model must read the sources and nothing else. The command turns off
every tool, every MCP server, every settings file, and every memory
file, and runs in an empty directory, so no hook, project file, or
memory reaches the model.
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence

from ish.adapters.language_model import _grounded
from ish.application.ports.language_model import (
    LanguageModelError,
    Reply,
    Selection,
    Source,
)

EXECUTABLE = "claude"
TIMEOUT_SECONDS = 600

# Ask the CLI to load no memory: no CLAUDE.md of any scope, and no auto
# memory. Do not rely on these alone. Version 2.1.296 still loaded the
# auto memory of its working directory with both set, so the empty
# directory each call runs in is what keeps that memory out. A managed
# policy of an organization is loaded whatever is set, and only --bare
# skips it, which also skips the login.
NO_MEMORY = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}


def command(
    executable: str,
    model: str,
    system: str = _grounded.SYSTEM_PROMPT,
    schema: dict[str, object] = _grounded.SCHEMA,
) -> list[str]:
    """Return the argument list that asks one question with nothing else loaded."""
    return [
        executable,
        "--print",
        "--setting-sources",
        "",
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--model",
        model,
        "--system-prompt",
        system,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
    ]


class ClaudeCliLanguageModel:
    """Write answers with the ``claude`` command in print mode."""

    def __init__(self, model: str = "", *, timeout: float = TIMEOUT_SECONDS) -> None:
        """Use *model*, a Claude model id or alias such as ``sonnet``.

        Require the model and the ``claude`` command on the path.
        """
        if not model:
            raise LanguageModelError(
                "Set answer_model to a Claude model for the claude-cli backend."
            )
        found = shutil.which(EXECUTABLE)
        if found is None:
            raise LanguageModelError(
                f"The claude-cli backend needs the {EXECUTABLE!r} command on PATH."
            )
        self.model_name = model
        self._executable = found
        self._timeout = timeout

    def answer(self, question: str, sources: Sequence[Source]) -> Reply:
        """Answer *question* from *sources* in the JSON the schema fixes."""
        structured, read, wrote = self._run(
            _grounded.SYSTEM_PROMPT,
            _grounded.SCHEMA,
            _grounded.prompt(question, sources),
        )
        return _grounded.parse(
            json.dumps(structured),
            f"claude {self.model_name}",
            input_tokens=read,
            output_tokens=wrote,
        )

    def select(
        self, question: str, candidates: Sequence[Source], limit: int
    ) -> Selection:
        """Choose at most *limit* candidates, in the JSON the schema fixes."""
        structured, read, wrote = self._run(
            _grounded.SELECT_PROMPT,
            _grounded.SELECT_SCHEMA,
            _grounded.select_prompt(question, candidates, limit),
        )
        return _grounded.parse_ids(
            json.dumps(structured),
            f"claude {self.model_name}",
            limit,
            input_tokens=read,
            output_tokens=wrote,
        )

    def _run(
        self, system: str, schema: dict[str, object], prompt: str
    ) -> tuple[object, int, int]:
        """Run one call, and return its structured output and token counts."""
        with tempfile.TemporaryDirectory(prefix="ish-ask-") as empty:
            try:
                done = subprocess.run(
                    command(self._executable, self.model_name, system, schema),
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=empty,
                    env={**os.environ, **NO_MEMORY},
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise LanguageModelError(
                    f"claude did not answer within {self._timeout:g} s."
                ) from exc
        try:
            body = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            detail = (done.stderr or done.stdout).strip()[:500]
            raise LanguageModelError(
                f"claude exited {done.returncode} without a JSON reply: {detail}"
            ) from exc
        if body.get("is_error") or "structured_output" not in body:
            raise LanguageModelError(f"claude did not answer: {body.get('result')}")
        usage = body.get("usage") or {}
        read = sum(
            int(usage.get(k) or 0)
            for k in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        return body["structured_output"], read, int(usage.get("output_tokens") or 0)
