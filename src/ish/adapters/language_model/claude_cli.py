"""Answer from sources with the Claude Code CLI, on the account it is logged in to.

The CLI has no citation feature, so the model replies in the JSON that
``_grounded`` fixes, through ``--json-schema``. It suits a person who
asks on their own machine. A shared server answers for many people, and
one login would carry them all, so a server uses the anthropic backend.

The model must read the sources and nothing else. The command turns off
every tool, every MCP server, and every settings file, and runs in an
empty directory, so no hook, project file, or memory reaches the model.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Sequence

from ish.adapters.language_model import _grounded
from ish.application.ports.language_model import LanguageModelError, Reply, Source

EXECUTABLE = "claude"
TIMEOUT_SECONDS = 600


def command(executable: str, model: str) -> list[str]:
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
        _grounded.SYSTEM_PROMPT,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(_grounded.SCHEMA),
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
        with tempfile.TemporaryDirectory(prefix="ish-ask-") as empty:
            try:
                done = subprocess.run(
                    command(self._executable, self.model_name),
                    input=_grounded.prompt(question, sources),
                    capture_output=True,
                    text=True,
                    cwd=empty,
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
        return _grounded.parse(
            json.dumps(body["structured_output"]),
            f"claude {self.model_name}",
            input_tokens=read,
            output_tokens=int(usage.get("output_tokens") or 0),
        )
