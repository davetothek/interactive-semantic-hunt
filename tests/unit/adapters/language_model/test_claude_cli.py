"""Test the claude-cli backend against a faked subprocess."""

import json
import subprocess
from pathlib import Path

import pytest

from ish.adapters.language_model import LANGUAGE_MODELS, _grounded, claude_cli
from ish.adapters.language_model._calls import (
    DEFAULT_READ_USD,
    CallOptions,
    write_trace,
)
from ish.adapters.language_model._prompts import load as load_prompts
from ish.adapters.language_model.claude_cli import (
    ClaudeCliLanguageModel,
    command,
    environment,
    usage_of,
)
from ish.application.ports.language_model import (
    Claim,
    LanguageModelError,
    Source,
    Verdicts,
)

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


class TestReadingTheTree:
    """An answer may read the files of its tree, and of no other place.

    A model that saw only the chunks a search found wrote what those
    chunks implied. Reading the section around a source and following its
    references found what the chunks left out.
    """

    def test_the_tools_are_reads_scoped_to_the_working_directory(self) -> None:
        args = command("/usr/bin/claude", "sonnet", "S", {}, tools=True)
        at = args.index("--tools")
        assert args[at + 1] == "Read,Grep,Glob"
        allowed = args[
            args.index("--allowedTools") + 1 : args.index("--max-budget-usd")
        ]
        assert allowed == ["Read(./**)", "Grep(./**)", "Glob(./**)"]
        assert args[args.index("--max-budget-usd") + 1] == str(DEFAULT_READ_USD)

    def test_an_answer_runs_in_the_tree_with_the_tools_prompt(
        self, runner, tmp_path: Path
    ) -> None:
        model = ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path)
        model.answer("How?", SOURCES)
        call = runner.calls[0]
        assert call["cwd"] == str(tmp_path)
        system = call["args"][call["args"].index("--system-prompt") + 1]
        assert system == f"{PROMPTS.answer}\n\n{PROMPTS.tools}"
        assert "Read,Grep,Glob" in call["args"]

    def test_the_rounds_of_reading_are_counted(self, runner, tmp_path: Path) -> None:
        """A reader of the answer sees whether the model read anything."""
        body = json.loads(runner.stdout) | {"num_turns": 5}
        runner.stdout = json.dumps(body)
        reading = ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path)
        assert reading.answer("How?", SOURCES).reads == 3
        assert (
            ClaudeCliLanguageModel("sonnet", timeout=5).answer("How?", SOURCES).reads
            == 0
        )

    def test_the_cache_use_is_kept_apart(self, runner, tmp_path: Path, caplog) -> None:
        """Of 352 tokens in, claude wrote 300 to its cache and read 50 from it."""
        caplog.set_level("INFO", logger=claude_cli.__name__)
        model = ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path)
        reply = model.answer("How?", SOURCES)
        assert reply.input_tokens == 352
        assert (reply.cache_write_tokens, reply.cache_read_tokens) == (300, 50)
        assert (
            "claude sonnet read 352 tokens in 1 requests: 2 uncached, 300 written "
            "to the cache, 50 read from it. It wrote 40, for $0.0000"
        ) in caplog.text

    def test_a_step_keeps_its_cache_use(self, runner, tmp_path: Path) -> None:
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {"covered": False, "passages": [], "search": []},
                "usage": {
                    "cache_creation_input_tokens": 7,
                    "cache_read_input_tokens": 3,
                },
            }
        )
        reply = ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path).step(
            "q", SOURCES, [], 1
        )
        assert (reply.cache_write_tokens, reply.cache_read_tokens) == (7, 3)

    def test_a_step_reads_the_tree_too(self, runner, tmp_path: Path) -> None:
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {"covered": False, "passages": [], "search": []},
            }
        )
        ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path).step(
            "q", SOURCES, [], 1
        )
        assert runner.calls[0]["cwd"] == str(tmp_path)

    def test_a_check_reads_nothing_but_its_message(
        self, runner, tmp_path: Path
    ) -> None:
        """The judge reads a claim beside its quotes alone."""
        runner.stdout = json.dumps(
            {"is_error": False, "structured_output": {"verdicts": []}}
        )
        ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path).check([])
        call = runner.calls[0]
        assert call["cwd"] != str(tmp_path)
        assert call["args"][call["args"].index("--tools") + 1] == ""

    def test_the_tools_prompt_names_the_form_of_a_citation(self) -> None:
        assert '"path:first-last"' in PROMPTS.tools
        assert "Read, Grep, and Glob" in PROMPTS.tools


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
            **claude_cli.CACHE_VARIABLES["5m"],
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

    def test_step_sends_the_notes_of_ish(self, runner) -> None:
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {"covered": False, "passages": [], "search": []},
            }
        )
        model = ClaudeCliLanguageModel("haiku", timeout=5)
        model.step("q", SOURCES, [], 0, notes=("Fix the claim.",))
        assert runner.calls[0]["input"] == _grounded.step_prompt(
            "q", SOURCES, [], 0, ("Fix the claim.",)
        )


class TestCheck:
    def test_check_runs_the_check_prompt_and_schema(self, runner) -> None:
        runner.stdout = json.dumps(
            {
                "is_error": False,
                "structured_output": {
                    "verdicts": [{"id": "1", "verdict": "unsupported"}]
                },
                "usage": {"input_tokens": 50, "output_tokens": 4},
                "total_cost_usd": 0.003,
            }
        )
        claims = [Claim("1", "It reads a queue.", ("It starts at boot.",))]
        verdicts = ClaudeCliLanguageModel("haiku", timeout=5).check(claims)
        call = runner.calls[0]
        assert call["args"] == command(
            "/usr/bin/claude", "haiku", PROMPTS.check, _grounded.CHECK_SCHEMA
        )
        assert call["input"] == _grounded.check_prompt(claims)
        assert verdicts == Verdicts(("unsupported",), 50, 4, 0.003)


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

    def test_a_streamed_answer_reads_the_tree(self, process, tmp_path: Path) -> None:
        ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path).answer(
            "q", SOURCES, on_block=lambda block: None
        )
        assert "Read,Grep,Glob" in process.args

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


class TestCallOptions:
    """The settings of a call reach claude as variables and flags."""

    @pytest.mark.parametrize(
        ("cache", "set_", "unset"),
        [
            ("5m", "FORCE_PROMPT_CACHING_5M", "DISABLE_PROMPT_CACHING"),
            ("off", "DISABLE_PROMPT_CACHING", "FORCE_PROMPT_CACHING_5M"),
        ],
    )
    def test_the_cache_is_a_variable(self, cache, set_, unset) -> None:
        env = environment({"PATH": "/bin"}, cache)
        assert env[set_] == "1" and unset not in env

    def test_an_hour_is_what_claude_does_unasked(self) -> None:
        env = environment({"PATH": "/bin", "FORCE_PROMPT_CACHING_5M": "1"}, "1h")
        assert "FORCE_PROMPT_CACHING_5M" not in env
        assert "DISABLE_PROMPT_CACHING" not in env

    def test_a_call_caches_for_five_minutes_unless_told(self, runner) -> None:
        ClaudeCliLanguageModel("sonnet", timeout=5).answer("How?", SOURCES)
        assert runner.calls[0]["env"]["FORCE_PROMPT_CACHING_5M"] == "1"

    def test_a_call_takes_its_cache_and_its_cost_of_reading(
        self, runner, tmp_path: Path
    ) -> None:
        options = CallOptions(prompt_cache="off", read_usd=0.25)
        model = ClaudeCliLanguageModel(
            "sonnet", timeout=5, tree=tmp_path, options=options
        )
        model.answer("How?", SOURCES)
        call = runner.calls[0]
        assert call["env"]["DISABLE_PROMPT_CACHING"] == "1"
        assert call["args"][call["args"].index("--max-budget-usd") + 1] == "0.25"

    def test_an_unknown_cache_is_refused(self) -> None:
        with pytest.raises(LanguageModelError, match="prompt_cache 'forever'"):
            CallOptions(prompt_cache="forever")


class TestTrace:
    """A trace lets a reader follow the data of an answer call by call."""

    def test_each_call_leaves_one_file(self, runner, tmp_path: Path) -> None:
        trace = tmp_path / "trace"
        model = ClaudeCliLanguageModel(
            "sonnet", timeout=5, tree=tmp_path, options=CallOptions(trace=trace)
        )
        model.answer("How?", SOURCES)
        runner.stdout = json.dumps(
            {"is_error": False, "structured_output": {"verdicts": []}}
        )
        model.check([])
        first, second = sorted(trace.iterdir())
        assert (first.name, second.name) == ("001-answer.json", "002-check.json")
        record = json.loads(first.read_text())
        assert (record["kind"], record["model"], record["tools"]) == (
            "answer",
            "sonnet",
            True,
        )
        assert record["tree"] == str(tmp_path)
        assert "Question: How?" in record["prompt"]
        assert record["system"].endswith(PROMPTS.tools)
        assert record["schema"] == _grounded.SCHEMA
        assert record["usage"] == {
            "uncached": 2,
            "written": 300,
            "cached": 50,
            "wrote": 40,
        }
        assert record["reply"]["covered"] is True and record["error"] == ""
        assert json.loads(second.read_text())["tree"] == ""

    def test_a_call_that_fails_is_traced_too(self, runner, tmp_path: Path) -> None:
        runner.stdout = json.dumps({"is_error": True, "result": "limit"})
        model = ClaudeCliLanguageModel(
            "sonnet", timeout=5, options=CallOptions(trace=tmp_path)
        )
        with pytest.raises(LanguageModelError, match="limit"):
            model.answer("How?", SOURCES)
        record = json.loads((tmp_path / "001-answer.json").read_text())
        assert (record["error"], record["reply"]) == ("limit", None)

    def test_a_name_that_is_taken_is_passed_over(self, tmp_path: Path) -> None:
        (tmp_path / "002-check.json").write_text("{}")
        assert write_trace(tmp_path, "check", {}).name == "003-check.json"

    def test_usage_that_is_not_a_table(self) -> None:
        used = usage_of({"usage": "none", "num_turns": 3})
        assert (used.read, used.requests) == (0, 3)


class TestEffort:
    """Each kind of call asks for the effort its setting names."""

    def _args(self, runner, kind: str, options: CallOptions) -> list[str]:
        model = ClaudeCliLanguageModel("sonnet", timeout=5, options=options)
        if kind == "check":
            runner.stdout = json.dumps(
                {"is_error": False, "structured_output": {"verdicts": []}}
            )
            model.check([])
        elif kind == "select":
            runner.stdout = json.dumps(
                {"is_error": False, "structured_output": {"ids": []}}
            )
            model.select("q", SOURCES, 1)
        else:
            model.answer("q", SOURCES)
        return runner.calls[-1]["args"]

    @pytest.mark.parametrize(
        ("kind", "effort"), [("answer", "high"), ("check", "low"), ("select", "max")]
    )
    def test_the_effort_of_each_kind(self, runner, kind, effort) -> None:
        options = CallOptions(
            answer_effort="high", check_effort="low", rerank_effort="max"
        )
        args = self._args(runner, kind, options)
        assert args[args.index("--effort") + 1] == effort

    def test_no_effort_asks_for_none(self, runner) -> None:
        assert "--effort" not in self._args(runner, "answer", CallOptions())

    def test_a_fallback_model(self, runner) -> None:
        args = self._args(runner, "answer", CallOptions(fallback_model="haiku"))
        assert args[args.index("--fallback-model") + 1] == "haiku"
        assert "--fallback-model" not in self._args(runner, "answer", CallOptions())

    def test_an_unknown_effort_is_refused(self) -> None:
        with pytest.raises(LanguageModelError, match="check_effort 'huge'"):
            CallOptions(check_effort="huge")

    def test_the_effort_and_the_fallback_are_traced(self, runner, tmp_path) -> None:
        options = CallOptions(trace=tmp_path, answer_effort="low", fallback_model="h")
        ClaudeCliLanguageModel("sonnet", timeout=5, options=options).answer(
            "q", SOURCES
        )
        record = json.loads((tmp_path / "001-answer.json").read_text())
        assert (record["effort"], record["fallback_model"]) == ("low", "h")


class TestCommandAndTimeout:
    def test_the_command_is_found_by_its_setting(self, runner, monkeypatch) -> None:
        looked: list[str] = []

        def which(name: str) -> str:
            looked.append(name)
            return f"/opt/{name}"

        monkeypatch.setattr(claude_cli.shutil, "which", which)
        options = CallOptions(claude="claude-beta")
        ClaudeCliLanguageModel("sonnet", timeout=5, options=options).answer(
            "q", SOURCES
        )
        assert looked == ["claude-beta"]
        assert runner.calls[0]["args"][0] == "/opt/claude-beta"

    def test_a_missing_command_is_named_by_its_setting(self, monkeypatch) -> None:
        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
        with pytest.raises(LanguageModelError, match="'claude-beta' command"):
            ClaudeCliLanguageModel("sonnet", options=CallOptions(claude="claude-beta"))

    def test_the_timeout_of_the_options_wins(self, runner) -> None:
        options = CallOptions(timeout=42)
        ClaudeCliLanguageModel("sonnet", timeout=5, options=options).answer(
            "q", SOURCES
        )
        assert runner.calls[0]["timeout"] == 42


class TestReadsLeaveOutTheReplyRound:
    """The reply that the schema fixes takes two requests of every call.

    A rerank with no tool reported 2 requests, so a call that read no file
    counted one round of reading, and an answer of four turns that read
    the tree once counted five.
    """

    @pytest.mark.parametrize(("requests", "reads"), [(1, 0), (2, 0), (3, 1), (6, 4)])
    def test_the_reply_round_is_no_reading(
        self, runner, tmp_path: Path, requests, reads
    ) -> None:
        runner.stdout = json.dumps(json.loads(runner.stdout) | {"num_turns": requests})
        model = ClaudeCliLanguageModel("sonnet", timeout=5, tree=tmp_path)
        assert model.answer("How?", SOURCES).reads == reads
