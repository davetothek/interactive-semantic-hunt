"""Test the claude-cli backend against a faked subprocess."""

import json
import subprocess
from pathlib import Path

import pytest

from ish.adapters.language_model import LANGUAGE_MODELS, _grounded, claude_cli
from ish.adapters.language_model.claude_cli import ClaudeCliLanguageModel, command
from ish.application.ports.language_model import LanguageModelError, Source

SOURCES = [Source("1", "/d/a.adoc:1-9 A", "It starts at boot.")]


class Runner:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.stdout = json.dumps(
            {
                "is_error": False,
                "result": "{}",
                "structured_output": {
                    "covered": True,
                    "passages": [{"text": "At boot.", "source_ids": ["1"]}],
                },
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 300,
                    "cache_read_input_tokens": 50,
                    "output_tokens": 40,
                },
            }
        )
        self.stderr = ""
        self.returncode = 0
        self.error: Exception | None = None

    def __call__(self, args, **kwargs):
        self.calls.append(
            {"args": args, **kwargs, "cwd_existed": Path(kwargs["cwd"]).is_dir()}
        )
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(
            args, self.returncode, self.stdout, self.stderr
        )


@pytest.fixture()
def runner(monkeypatch) -> Runner:
    made = Runner()
    monkeypatch.setattr(claude_cli.subprocess, "run", made)
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    return made


class TestConfiguration:
    def test_the_registry_names_the_backend(self) -> None:
        assert LANGUAGE_MODELS["claude-cli"] is ClaudeCliLanguageModel

    def test_no_model_is_chosen_for_the_operator(self) -> None:
        with pytest.raises(LanguageModelError, match="answer_model"):
            ClaudeCliLanguageModel("")

    def test_a_missing_command_is_named(self, monkeypatch) -> None:
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
        with pytest.raises(LanguageModelError, match="'claude' command"):
            ClaudeCliLanguageModel("sonnet")


class TestCommand:
    """The model reads the sources and nothing else."""

    def test_every_tool_server_and_settings_file_is_off(self) -> None:
        args = command("/usr/bin/claude", "sonnet")
        flags = dict(zip(args[1::1], args[2::1], strict=False))
        assert args[0] == "/usr/bin/claude"
        assert "--print" in args
        assert flags["--tools"] == ""
        assert flags["--setting-sources"] == ""
        assert "--strict-mcp-config" in args
        assert "--no-session-persistence" in args
        assert flags["--model"] == "sonnet"
        assert flags["--system-prompt"] == _grounded.SYSTEM_PROMPT
        assert flags["--output-format"] == "json"
        assert json.loads(flags["--json-schema"]) == _grounded.SCHEMA

    def test_the_question_goes_on_stdin_in_an_empty_directory(self, runner) -> None:
        reply = ClaudeCliLanguageModel("sonnet", timeout=5).answer("How?", SOURCES)
        call = runner.calls[0]
        assert call["args"] == command("/usr/bin/claude", "sonnet")
        assert call["input"] == _grounded.prompt("How?", SOURCES)
        assert call["timeout"] == 5
        assert call["cwd_existed"]
        assert call["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
        assert call["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
        assert call["env"]["PATH"]
        assert not Path(call["cwd"]).exists()
        assert reply.covered
        assert reply.passages[0].source_ids == ("1",)
        assert (reply.input_tokens, reply.output_tokens) == (352, 40)


class TestFailures:
    def _fails(self, message: str) -> None:
        with pytest.raises(LanguageModelError, match=message):
            ClaudeCliLanguageModel("sonnet", timeout=5).answer("q", SOURCES)

    def test_not_logged_in(self, runner) -> None:
        runner.stdout = json.dumps(
            {"is_error": True, "result": "Not logged in · Please run /login"}
        )
        self._fails("Not logged in")

    def test_no_structured_output(self, runner) -> None:
        runner.stdout = json.dumps({"is_error": False, "result": "plain text"})
        self._fails("did not answer: plain text")

    def test_output_that_is_not_json(self, runner) -> None:
        runner.stdout, runner.stderr, runner.returncode = "", "boom", 1
        self._fails("exited 1 without a JSON reply: boom")

    def test_a_timeout(self, runner) -> None:
        runner.error = subprocess.TimeoutExpired("claude", 5)
        self._fails("within 5 s")

    def test_output_of_the_wrong_shape(self, runner) -> None:
        runner.stdout = json.dumps({"is_error": False, "structured_output": {"x": 1}})
        self._fails("did not reply in the JSON")


class TestSelect:
    def test_select_runs_the_select_prompt_and_schema(self, runner) -> None:
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {"ids": ["2", "1"]},
                "usage": {"input_tokens": 500, "output_tokens": 8},
            }
        )
        selection = ClaudeCliLanguageModel("haiku", timeout=5).select("q", SOURCES, 1)
        call = runner.calls[0]
        assert call["args"] == command(
            "/usr/bin/claude", "haiku", _grounded.SELECT_PROMPT, _grounded.SELECT_SCHEMA
        )
        assert call["input"] == _grounded.select_prompt("q", SOURCES, 1)
        assert selection.ids == ("2",)
        assert (selection.input_tokens, selection.output_tokens) == (500, 8)

    def test_select_keeps_every_tool_off(self) -> None:
        args = command("/c", "m", _grounded.SELECT_PROMPT, _grounded.SELECT_SCHEMA)
        assert args[args.index("--tools") + 1] == ""
        assert args[args.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in args
