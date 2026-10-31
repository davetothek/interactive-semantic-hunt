"""Test that the instructions are files an operator can replace one by one."""

import logging
from pathlib import Path

import pytest

from ish.adapters.language_model import prompts
from ish.adapters.language_model.prompts import NAMES, Prompts, load
from ish.application.ports.language_model import SEARCHES_A_TURN, LanguageModelError


class TestShipped:
    def test_every_instruction_ships_as_a_file(self) -> None:
        loaded = load()
        for name in NAMES:
            assert getattr(loaded, name)

    def test_the_lines_of_a_paragraph_join_into_one(self) -> None:
        assert "\n" not in load().answer
        assert "  " not in load().answer

    def test_the_numbers_are_the_ones_the_code_enforces(self) -> None:
        from ish.adapters.language_model._grounded import MAX_GAPS

        loaded = load()
        assert MAX_GAPS == 5 and "at most five" in loaded.answer
        assert SEARCHES_A_TURN == 3 and "up to three new search" in loaded.step
        assert prompts.NOT_COVERED in loaded.answer_cited

    def test_the_step_instruction_extends_the_answer(self) -> None:
        loaded = load()
        assert loaded.step.startswith(loaded.answer + " ")


class TestOverride:
    def test_a_file_replaces_its_default_and_the_rest_stay(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "select.md").write_text("Choose\nwell.\n\nShort list.\n")
        loaded = load(tmp_path)
        assert loaded.select == "Choose well.\n\nShort list."
        assert loaded.answer == load().answer

    def test_an_own_answer_reaches_the_step(self, tmp_path: Path) -> None:
        (tmp_path / "answer.md").write_text("Answer briefly, at most $max_gaps gaps.")
        loaded = load(tmp_path)
        assert loaded.answer == "Answer briefly, at most five gaps."
        assert loaded.step.startswith("Answer briefly, at most five gaps. You may")

    def test_a_dollar_sign_is_written_twice(self, tmp_path: Path) -> None:
        (tmp_path / "answer.md").write_text("Prices are in $$.")
        loaded = load(tmp_path)
        assert loaded.answer == "Prices are in $."
        assert loaded.step.startswith("Prices are in $. ")

    def test_a_value_no_code_fills_names_the_file(self, tmp_path: Path) -> None:
        (tmp_path / "select.md").write_text("Pick $how_many.")
        with pytest.raises(
            LanguageModelError, match="select instruction uses 'how_many'"
        ):
            load(tmp_path)

    def test_a_lone_dollar_sign_names_the_file(self, tmp_path: Path) -> None:
        (tmp_path / "step.md").write_text("$answer Costs $ 5.")
        with pytest.raises(LanguageModelError, match="step instruction.*\\$\\$"):
            load(tmp_path)

    def test_a_file_that_names_no_instruction_is_reported(
        self, tmp_path: Path, caplog
    ) -> None:
        (tmp_path / "answr.md").write_text("typo")
        with caplog.at_level(logging.WARNING):
            load(tmp_path)
        assert "answr.md names no instruction" in caplog.text

    def test_a_missing_directory_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(LanguageModelError, match="not a directory"):
            load(tmp_path / "nowhere")

    def test_a_value_can_be_given_and_a_large_count_stays_a_number(self) -> None:
        loaded = load(searches_a_turn=12, max_gaps=2)
        assert "up to 12 new search" in loaded.step
        assert "at most two" in loaded.answer


class TestInjection:
    def test_bootstrap_hands_the_backend_the_prompts_of_prompt_dir(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from ish import bootstrap
        from ish.settings import Settings

        (tmp_path / "answer.md").write_text("Own answer.")
        given: list[Prompts] = []

        def stub(model: str, *, prompts: Prompts):
            given.append(prompts)
            return object()

        monkeypatch.setitem(bootstrap.LANGUAGE_MODELS, "stub", stub)
        settings = Settings(
            language_model="stub",
            answer_model="m",
            allowed_language_models=("stub",),
            prompt_dir=str(tmp_path),
        )
        bootstrap.build_language_model(settings)
        assert given[0].answer == "Own answer."
        assert given[0].select == load().select

    def test_no_prompt_dir_hands_the_shipped_prompts(self) -> None:
        from ish import bootstrap
        from ish.settings import Settings

        assert bootstrap.build_prompts(Settings()) == load()

    @pytest.mark.parametrize("backend", ["anthropic", "ollama", "claude-cli"])
    def test_every_backend_sends_the_prompts_it_is_given(
        self, backend: str, monkeypatch
    ) -> None:
        from ish.adapters.language_model import LANGUAGE_MODELS, claude_cli

        monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/usr/bin/claude")
        own = Prompts(answer="A", answer_cited="C", step="S", select="P")
        made = LANGUAGE_MODELS[backend]("m", prompts=own)
        assert made._prompts is own
