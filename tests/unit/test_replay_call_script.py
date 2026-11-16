"""Test the script that runs one traced call of claude-cli again.

The script lives outside the package, because it measures ish rather
than ships with it. Load it by path.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from ish.adapters.language_model import claude_cli

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "replay_call.py"


def _load():
    spec = importlib.util.spec_from_file_location("replay_call", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rc = _load()

REPLY = {
    "is_error": False,
    "structured_output": {"verdicts": []},
    "usage": {
        "input_tokens": 3,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 9,
    },
    "num_turns": 2,
    "total_cost_usd": 0.0012,
}


@pytest.fixture()
def call(tmp_path: Path) -> Path:
    path = tmp_path / "002-check.json"
    path.write_text(
        json.dumps(
            {
                "kind": "check",
                "model": "sonnet",
                "tools": True,
                "tree": str(tmp_path),
                "prompt_cache": "5m",
                "read_usd": 1.0,
                "system": "Judge.",
                "schema": {"type": "object"},
                "prompt": "Claims.",
                "usage": {"uncached": 2, "written": 6000, "cached": 0, "wrote": 800},
                "requests": 2,
                "cost_usd": 0.035,
                "seconds": 7.5,
                "error": "",
            }
        )
    )
    return path


@pytest.fixture()
def sent(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def run_whole(args, prompt, *, cwd, env, timeout):
        calls.append({"args": args, "prompt": prompt, "cwd": cwd, "env": env})
        return REPLY

    monkeypatch.setattr(claude_cli, "run_whole", run_whole)
    monkeypatch.setattr(rc.shutil, "which", lambda name: f"/usr/bin/{name}")
    return calls


class TestReplay:
    def test_the_call_is_sent_as_traced(self, call, sent, capsys) -> None:
        assert rc.main([str(call)]) == 0
        [one] = sent
        assert one["prompt"] == "Claims."
        assert one["args"][one["args"].index("--model") + 1] == "sonnet"
        assert one["args"][one["args"].index("--system-prompt") + 1] == "Judge."
        assert one["cwd"] == str(call.parent)
        assert one["env"]["FORCE_PROMPT_CACHING_5M"] == "1"
        out = capsys.readouterr().out
        assert "| traced | sonnet | 5m | 2 | 2 | 6000 | 0 | 800 | 0.0350 | 7.5 |" in out
        assert "| #1 | sonnet | 5m | 2 | 3 | 0 | 0 | 9 | 0.0012 |" in out

    def test_one_part_of_the_call_changes(self, call, sent, tmp_path) -> None:
        system = tmp_path / "system.md"
        system.write_text("Judge strictly.")
        argv = [str(call), "--model", "haiku", "--prompt-cache", "off"]
        argv += ["--system", str(system), "--no-tools", "--read-usd", "0.2"]
        rc.main(argv)
        [one] = sent
        assert one["args"][one["args"].index("--model") + 1] == "haiku"
        assert one["args"][one["args"].index("--system-prompt") + 1] == (
            "Judge strictly."
        )
        assert one["args"][one["args"].index("--tools") + 1] == ""
        assert one["cwd"] != str(call.parent)
        assert one["env"]["DISABLE_PROMPT_CACHING"] == "1"

    def test_a_new_message(self, call, sent, tmp_path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("Fewer claims.")
        rc.main([str(call), "--prompt", str(prompt), "--read-usd", "0.2"])
        args = sent[0]["args"]
        assert sent[0]["prompt"] == "Fewer claims."
        assert args[args.index("--max-budget-usd") + 1] == "0.2"

    def test_each_repeat_is_traced(self, call, sent, tmp_path) -> None:
        out = tmp_path / "again"
        rc.main([str(call), "--repeat", "2", "--trace", str(out)])
        assert len(sent) == 2
        names = sorted(p.name for p in out.iterdir())
        assert names == ["001-check.json", "002-check.json"]
        again = json.loads((out / "001-check.json").read_text())
        assert again["reply"] == {"verdicts": []} and again["cost_usd"] == 0.0012

    def test_a_failed_reply_is_named(self, call, sent, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            claude_cli, "run_whole", lambda *a, **k: {"is_error": True, "result": "x"}
        )
        rc.main([str(call)])
        assert "| #1 | sonnet | 5m | 1 | 0 | 0 | 0 | 0 | 0.0000 | " in (
            capsys.readouterr().out
        )

    def test_a_call_that_does_not_run(self, call, monkeypatch, capsys) -> None:
        monkeypatch.setattr(rc.shutil, "which", lambda name: None)
        assert rc.main([str(call)]) == 1
        assert "Call 1 failed: No 'claude' command on PATH." in capsys.readouterr().err

    def test_no_call_of_a_trace(self, tmp_path, capsys) -> None:
        bad = tmp_path / "x.json"
        bad.write_text('{"kind": "check"}')
        with pytest.raises(SystemExit):
            rc.main([str(bad)])
        assert "is no call of a trace: no model" in capsys.readouterr().err

    def test_a_repeat_of_none(self, call, capsys) -> None:
        with pytest.raises(SystemExit):
            rc.main([str(call), "--repeat", "0"])
        assert "--repeat takes a number of 1 or more." in capsys.readouterr().err
