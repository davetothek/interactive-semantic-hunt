"""Test the model router: one row per rule, plus the override."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import route_model
from route_model import HAIKU, OPUS, SONNET

HOOK = Path(route_model.__file__)


def call(prompt: str = "Fix the thing.", **fields) -> dict:
    return {"description": "a task", "prompt": prompt, **fields}


@pytest.mark.parametrize(
    ("tool_input", "model", "rule"),
    [
        (call(subagent_type="Explore"), HAIKU, "read-only agent"),
        (call(subagent_type="Plan"), OPUS, "design agent"),
        (call("Change bootstrap.py to wire it"), OPUS, "protected: bootstrap.py"),
        (
            call("Touch application/ports/parser.py"),
            OPUS,
            "protected: application/ports",
        ),
        (call("Bump SCHEMA_VERSION"), OPUS, "protected: SCHEMA_VERSION"),
        (call("Tune SEMANTIC_WEIGHT"), OPUS, "protected: SEMANTIC_WEIGHT"),
        (call("Edit the task prefixes table"), OPUS, "protected: prefixes"),
        (call("Refactor the scan"), OPUS, "design word: refactor"),
        (call("Explain why this is slow"), OPUS, "design word: why"),
        (call("Write a spec for X"), OPUS, "design word: spec"),
        (call("x" * (route_model.LONG_PROMPT_CHARS + 1)), OPUS, "long prompt"),
        (call("Add a test for the empty case"), SONNET, "bounded change"),
        (call("Rename foo to bar in scan.py"), SONNET, "bounded change"),
        (call("Fix issue #64: one warning per file"), SONNET, "bounded change"),
    ],
)
def test_one_rule(tool_input: dict, model: str, rule: str) -> None:
    assert route_model.route(tool_input) == (model, rule)


def test_an_explicit_model_is_left_alone() -> None:
    assert route_model.route(call(subagent_type="Explore", model="opus")) is None
    assert route_model.route(call("Change bootstrap.py", model="haiku")) is None


def test_the_read_only_agent_wins_over_a_design_word() -> None:
    """Explore cannot write, so its prompt's words do not raise the tier."""
    tool_input = call("Find where the schema is designed", subagent_type="Explore")
    assert route_model.route(tool_input) == (HAIKU, "read-only agent")


def test_a_protected_path_inside_a_quoted_issue_still_routes_up() -> None:
    prompt = 'The issue says: "the bug is in ranking.py". Fix the typo in README.'
    assert route_model.route(call(prompt))[0] == OPUS


def test_the_description_is_read_too() -> None:
    assert (
        route_model.route({"description": "refactor scan", "prompt": "do it"})[0]
        == OPUS
    )


def test_a_design_word_inside_another_word_does_not_fire() -> None:
    assert route_model.route(call("Write the specification section"))[0] == SONNET


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


class TestProcess:
    def test_the_whole_input_is_echoed_with_the_model(self, tmp_path: Path) -> None:
        tool_input = {
            "description": "a task",
            "prompt": "Add a test",
            "subagent_type": "general-purpose",
            "run_in_background": True,
        }
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": tool_input,
                "scratchpad_dir": str(tmp_path),
            }
        )
        assert result.returncode == 0
        updated = json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]
        assert updated == {**tool_input, "model": SONNET}

    def test_a_decision_is_logged(self, tmp_path: Path) -> None:
        _run(
            {
                "tool_name": "Agent",
                "tool_input": call("Add a test"),
                "scratchpad_dir": str(tmp_path),
            }
        )
        line = json.loads(
            (tmp_path / "model-routing.jsonl").read_text().splitlines()[0]
        )
        assert line["model"] == SONNET
        assert line["rule"] == "bounded change"
        assert "prompt" not in line

    def test_an_explicit_model_writes_nothing(self, tmp_path: Path) -> None:
        result = _run(
            {
                "tool_name": "Agent",
                "tool_input": call(model="opus"),
                "scratchpad_dir": str(tmp_path),
            }
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert not (tmp_path / "model-routing.jsonl").exists()

    def test_no_scratchpad_still_routes(self) -> None:
        result = _run({"tool_name": "Agent", "tool_input": call("Add a test")})
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["model"]

    def test_garbage_input_allows(self) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input="{",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""
