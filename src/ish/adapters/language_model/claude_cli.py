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
from collections.abc import Mapping, Sequence
from dataclasses import replace

from ish.adapters.language_model import _grounded
from ish.adapters.language_model._prompts import Prompts
from ish.adapters.language_model._prompts import load as load_prompts
from ish.application.ports.language_model import (
    LanguageModelError,
    Query,
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

# The variables a call passes on, and no others. A server holds secrets
# in its environment, such as its MCP token and an API key, and a child
# process gets none of them. These are what the CLI needs to run, to
# find the login it is signed in with, and to reach the network through
# a proxy that a company may require.
PASSED_ON = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "CLAUDE_CONFIG_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


def environment(source: Mapping[str, str]) -> dict[str, str]:
    """Return the environment of a call: the allowed names, and no memory."""
    passed = {name: source[name] for name in PASSED_ON if name in source}
    return {**passed, **NO_MEMORY}


def command(
    executable: str, model: str, system: str, schema: dict[str, object]
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

    def __init__(
        self,
        model: str = "",
        *,
        prompts: Prompts | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
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
        self._prompts = prompts or load_prompts()
        self._executable = found
        self._timeout = timeout

    def answer(self, question: str, sources: Sequence[Source]) -> Reply:
        """Answer *question* from *sources* in the JSON the schema fixes."""
        structured, read, wrote, cost = self._run(
            self._prompts.answer,
            _grounded.SCHEMA,
            _grounded.prompt(question, sources),
        )
        reply = _grounded.parse(
            json.dumps(structured),
            f"claude {self.model_name}",
            input_tokens=read,
            output_tokens=wrote,
        )
        return replace(reply, cost_usd=cost)

    def step(
        self,
        question: str,
        sources: Sequence[Source],
        searched: Sequence[str],
        turns_left: int,
    ) -> Reply:
        """Answer, or ask for searches, in the JSON the schema fixes."""
        structured, read, wrote, cost = self._run(
            self._prompts.step,
            _grounded.STEP_SCHEMA,
            _grounded.step_prompt(question, sources, searched, turns_left),
        )
        reply = _grounded.parse_step(
            json.dumps(structured), f"claude {self.model_name}", read, wrote
        )
        return replace(reply, cost_usd=cost)

    def select(
        self, question: str, candidates: Sequence[Source], limit: int
    ) -> Selection:
        """Choose at most *limit* candidates, in the JSON the schema fixes."""
        structured, read, wrote, cost = self._run(
            self._prompts.select,
            _grounded.SELECT_SCHEMA,
            _grounded.select_prompt(question, candidates, limit),
        )
        selection = _grounded.parse_ids(
            json.dumps(structured),
            f"claude {self.model_name}",
            limit,
            input_tokens=read,
            output_tokens=wrote,
        )
        return replace(selection, cost_usd=cost)

    def query(self, question: str, history: str) -> Query:
        """Write one search query, in the JSON the schema fixes."""
        structured, read, wrote, cost = self._run(
            self._prompts.query,
            _grounded.QUERY_SCHEMA,
            _grounded.query_prompt(question, history),
        )
        query = _grounded.parse_query(
            json.dumps(structured), f"claude {self.model_name}", read, wrote
        )
        return replace(query, cost_usd=cost)

    def _run(
        self, system: str, schema: dict[str, object], prompt: str
    ) -> tuple[object, int, int, float]:
        """Run one call, and return its structured output, tokens, and cost.

        The cost is what claude says the call would cost at the prices of
        the API, also when a subscription pays for it.
        """
        with tempfile.TemporaryDirectory(prefix="ish-ask-") as empty:
            try:
                done = subprocess.run(
                    command(self._executable, self.model_name, system, schema),
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=empty,
                    env=environment(os.environ),
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
        wrote = int(usage.get("output_tokens") or 0)
        cost = float(body.get("total_cost_usd") or 0.0)
        return body["structured_output"], read, wrote, cost
