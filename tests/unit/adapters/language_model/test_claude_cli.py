"""Test the claude-cli backend against a faked subprocess."""

import json
import subprocess
from pathlib import Path

import pytest

from ish.adapters.language_model import LANGUAGE_MODELS, _grounded, claude_cli
from ish.adapters.language_model._prompts import load as load_prompts
from ish.adapters.language_model.claude_cli import ClaudeCliLanguageModel, command
from ish.application.ports.language_model import LanguageModelError, Source

PROMPTS = load_prompts()

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
                    "summary": {"text": "At boot.", "source_ids": ["1"]},
                    "sections": [],
                    "gaps": [],
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
        args = command("/usr/bin/claude", "sonnet", PROMPTS.answer, _grounded.SCHEMA)
        flags = dict(zip(args[1::1], args[2::1], strict=False))
        assert args[0] == "/usr/bin/claude"
        assert "--print" in args
        assert flags["--tools"] == ""
        assert flags["--setting-sources"] == ""
        assert "--strict-mcp-config" in args
        assert "--no-session-persistence" in args
        assert flags["--model"] == "sonnet"
        assert flags["--system-prompt"] == PROMPTS.answer
        assert flags["--output-format"] == "json"
        assert json.loads(flags["--json-schema"]) == _grounded.SCHEMA

    def test_the_question_goes_on_stdin_in_an_empty_directory(self, runner) -> None:
        reply = ClaudeCliLanguageModel("sonnet", timeout=5).answer("How?", SOURCES)
        call = runner.calls[0]
        assert call["args"] == command(
            "/usr/bin/claude", "sonnet", PROMPTS.answer, _grounded.SCHEMA
        )
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
            "/usr/bin/claude", "haiku", PROMPTS.select, _grounded.SELECT_SCHEMA
        )
        assert call["input"] == _grounded.select_prompt("q", SOURCES, 1)
        assert selection.ids == ("2",)
        assert (selection.input_tokens, selection.output_tokens) == (500, 8)

    def test_select_keeps_every_tool_off(self) -> None:
        args = command("/c", "m", PROMPTS.select, _grounded.SELECT_SCHEMA)
        assert args[args.index("--tools") + 1] == ""
        assert args[args.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in args


class TestEnvironment:
    """A server keeps secrets in its environment, and a call gets none."""

    def test_only_the_allowed_names_pass(self) -> None:
        env = claude_cli.environment(
            {
                "PATH": "/usr/bin",
                "HOME": "/home/u",
                "HTTPS_PROXY": "http://proxy.example.com:8080",
                "ISH_MCP_TOKEN": "secret-EXAMPLE",
                "ANTHROPIC_API_KEY": "sk-ant-EXAMPLE",
                "AWS_SECRET_ACCESS_KEY": "EXAMPLE",
            }
        )
        assert env == {
            "PATH": "/usr/bin",
            "HOME": "/home/u",
            "HTTPS_PROXY": "http://proxy.example.com:8080",
            **claude_cli.NO_MEMORY,
        }

    def test_a_call_gets_no_secret_of_the_server(self, runner, monkeypatch) -> None:
        monkeypatch.setenv("ISH_MCP_TOKEN", "secret-EXAMPLE")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-EXAMPLE")
        ClaudeCliLanguageModel("sonnet", timeout=5).answer("q", SOURCES)
        env = runner.calls[0]["env"]
        assert "ISH_MCP_TOKEN" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert not any(name.startswith("ISH_") for name in env)


class TestStep:
    def test_step_runs_the_step_prompt_and_schema(self, runner) -> None:
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {
                    "covered": False,
                    "passages": [],
                    "search": ["where is it handled"],
                },
                "usage": {"input_tokens": 70, "output_tokens": 6},
            }
        )
        reply = ClaudeCliLanguageModel("haiku", timeout=5).step("q", SOURCES, [], 2)
        call = runner.calls[0]
        assert call["args"] == command(
            "/usr/bin/claude", "haiku", PROMPTS.step, _grounded.STEP_SCHEMA
        )
        assert call["input"] == _grounded.step_prompt("q", SOURCES, [], 2)
        assert reply.searches == ("where is it handled",)


class TestQuery:
    def test_query_runs_the_query_prompt_and_schema(self, runner) -> None:
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {"query": "watchdog reset"},
                "usage": {"input_tokens": 90, "output_tokens": 5},
                "total_cost_usd": 0.0003,
            }
        )
        query = ClaudeCliLanguageModel("haiku", timeout=5).query("and it?", "Q: w")
        call = runner.calls[0]
        assert call["args"] == command(
            "/usr/bin/claude", "haiku", PROMPTS.query, _grounded.QUERY_SCHEMA
        )
        assert call["input"] == _grounded.query_prompt("and it?", "Q: w")
        assert (query.text, query.input_tokens, query.cost_usd) == (
            "watchdog reset",
            90,
            0.0003,
        )


class TestCost:
    """claude says what each call would cost at the prices of the API."""

    def test_each_kind_of_call_keeps_the_cost(self, runner) -> None:
        body = json.loads(runner.stdout)
        runner.stdout = json.dumps({**body, "total_cost_usd": 0.0123})
        model = ClaudeCliLanguageModel("sonnet", timeout=5)
        assert model.answer("q", SOURCES).cost_usd == 0.0123
        assert model.step("q", SOURCES, [], 0).cost_usd == 0.0123
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {"ids": ["1"]},
                "total_cost_usd": 0.002,
            }
        )
        assert model.select("q", SOURCES, 1).cost_usd == 0.002

    def test_a_reply_with_no_cost_costs_nothing_known(self, runner) -> None:
        assert (
            ClaudeCliLanguageModel("sonnet", timeout=5).answer("q", SOURCES).cost_usd
            == 0.0
        )


def _events(structured: dict, result: dict | None = None) -> list[str]:
    """Return the lines claude writes when it streams *structured*."""
    text = json.dumps(structured)
    lines = ["Warning: no stdin data received in 3s.\n"]
    lines.append(
        json.dumps({"type": "stream_event", "event": {"type": "message_start"}})
    )
    for start in range(0, len(text), 9):
        delta = {"type": "input_json_delta", "partial_json": text[start : start + 9]}
        lines.append(
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {"type": "content_block_delta", "delta": delta},
                }
            )
        )
    lines.append(
        json.dumps({"type": "stream_event", "event": {"type": "message_stop"}})
    )
    lines.append('["not", "an", "event"]')
    if result is not None:
        lines.append(json.dumps(result))
    return [line if line.endswith("\n") else line + "\n" for line in lines]


class Process:
    """A claude that writes its stream, and records how it was run and stopped."""

    def __init__(
        self, lines: list[str], returncode: int = 0, stops: bool = True
    ) -> None:
        import io

        self.stdin = io.StringIO()
        self.stdout = iter(lines)
        self.lines = lines
        self.returncode: int | None = None
        self._final = returncode
        self._stops = stops
        self.killed = False
        self.args: list[str] = []
        self.written = ""

    def __call__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        errors = kwargs["stderr"]
        errors.write("claude broke")
        errors.flush()
        original = self.stdin.close

        def close() -> None:
            self.written = self.stdin.getvalue()
            original()

        self.stdin.close = close
        return self

    def wait(self) -> int:
        if self._stops:
            self.returncode = self._final
        return self.returncode or 0

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


STRUCTURED = {
    "covered": True,
    "summary": {"text": "At boot.", "source_ids": ["1"]},
    "sections": [
        {
            "heading": "",
            "style": "prose",
            "passages": [{"text": "It starts at boot.", "source_ids": ["1"]}],
            "columns": [],
            "rows": [],
        }
    ],
    "gaps": [],
    "terms": [],
}
RESULT = {
    "type": "result",
    "is_error": False,
    "structured_output": STRUCTURED,
    "usage": {"input_tokens": 5, "output_tokens": 30},
    "total_cost_usd": 0.004,
}


class TestStreaming:
    """A reader gets each block as claude writes it, and the reply at its end."""

    @pytest.fixture()
    def process(self, monkeypatch) -> Process:
        made = Process(_events(STRUCTURED, RESULT))
        monkeypatch.setattr(claude_cli.subprocess, "Popen", made)
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
        return made

    def test_each_block_streams_and_the_reply_is_the_whole(self, process) -> None:
        blocks: list = []
        reply = ClaudeCliLanguageModel("sonnet", timeout=5).answer(
            "q", SOURCES, on_block=blocks.append
        )
        assert tuple(blocks) == reply.blocks
        assert [b.kind for b in blocks] == ["summary", "prose"]
        assert (reply.input_tokens, reply.output_tokens, reply.cost_usd) == (
            5,
            30,
            0.004,
        )
        assert process.args == command(
            "/usr/bin/claude", "sonnet", PROMPTS.answer, _grounded.SCHEMA, stream=True
        )
        assert process.written == _grounded.prompt("q", SOURCES)
        assert not process.killed

    def test_a_step_streams_as_an_answer_does(self, process) -> None:
        blocks: list = []
        ClaudeCliLanguageModel("sonnet", timeout=5).step(
            "q", SOURCES, [], 0, on_block=blocks.append
        )
        assert [b.kind for b in blocks] == ["summary", "prose"]

    def test_the_stream_arguments(self) -> None:
        args = command("/c", "m", "s", {}, stream=True)
        at = args.index("--output-format")
        assert args[at + 1 : at + 4] == [
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]

    def test_a_stream_with_no_reply_says_what_claude_wrote(self, monkeypatch) -> None:
        made = Process(_events(STRUCTURED), returncode=1)
        monkeypatch.setattr(claude_cli.subprocess, "Popen", made)
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
        with pytest.raises(
            LanguageModelError, match="exited 1 without a JSON reply: claude broke"
        ):
            ClaudeCliLanguageModel("sonnet", timeout=5).answer(
                "q", SOURCES, on_block=print
            )

    def test_a_reply_that_is_an_error_is_one(self, monkeypatch) -> None:
        failed = {"type": "result", "is_error": True, "result": "You've hit your limit"}
        made = Process(_events(STRUCTURED, failed))
        monkeypatch.setattr(claude_cli.subprocess, "Popen", made)
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
        with pytest.raises(LanguageModelError, match="You've hit your limit"):
            ClaudeCliLanguageModel("sonnet", timeout=5).answer(
                "q", SOURCES, on_block=print
            )

    def test_a_call_past_its_time_is_stopped(self, monkeypatch) -> None:
        made = Process([], stops=False)
        monkeypatch.setattr(claude_cli.subprocess, "Popen", made)
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: f"/usr/bin/{name}")

        class Now:
            """A timer that fires at once, as if the call took too long."""

            def __init__(self, seconds, call, args) -> None:
                self.call, self.args = call, args

            def start(self) -> None:
                self.call(*self.args)

            def cancel(self) -> None:
                pass

        monkeypatch.setattr(claude_cli.threading, "Timer", Now)
        with pytest.raises(LanguageModelError, match="did not answer within 5 s"):
            ClaudeCliLanguageModel("sonnet", timeout=5).answer(
                "q", SOURCES, on_block=print
            )
        assert made.killed

    def test_a_reader_that_stops_stops_claude(self, monkeypatch) -> None:
        made = Process(_events(STRUCTURED, RESULT), stops=False)
        monkeypatch.setattr(claude_cli.subprocess, "Popen", made)
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: f"/usr/bin/{name}")

        def stop(block) -> None:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            ClaudeCliLanguageModel("sonnet", timeout=5).answer(
                "q", SOURCES, on_block=stop
            )
        assert made.killed
