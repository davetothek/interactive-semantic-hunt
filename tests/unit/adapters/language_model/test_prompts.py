"""Test that the instructions are files an operator can replace one by one."""

import logging
from pathlib import Path

import pytest

from ish.adapters.language_model import _prompts as prompts
from ish.adapters.language_model._prompts import NAMES, Prompts, load
from ish.application.ports.language_model import SEARCHES_A_TURN, LanguageModelError


class TestShipped:
    def test_every_instruction_ships_as_a_file(self) -> None:
        loaded = load()
        for name in NAMES:
            assert getattr(loaded, name)

    def test_the_lines_of_a_paragraph_join_into_one(self) -> None:
        """A blank line keeps paragraphs apart, and nothing else breaks a line."""
        for paragraph in load().answer.split("\n\n"):
            assert "\n" not in paragraph
            assert "  " not in paragraph

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


class TestNotes:
    """Notes add to the instructions and replace none of them."""

    def test_answer_notes_follow_each_instruction_that_answers(self) -> None:
        shipped, noted = load(), load(answer_notes="  Answer in Norwegian.  ")
        tail = f"\n\n{prompts.NOTES_HEADING}\n\nAnswer in Norwegian."
        assert noted.answer == shipped.answer + tail
        assert noted.answer_cited == shipped.answer_cited + tail
        assert noted.step.startswith(noted.answer)
        assert (noted.select, noted.brief) == (shipped.select, shipped.brief)

    def test_rerank_notes_follow_the_rerank_alone(self) -> None:
        shipped, noted = load(), load(rerank_notes="Prefer docs/spec.")
        assert noted.select.endswith("\n\nPrefer docs/spec.")
        assert noted.answer == shipped.answer

    def test_a_note_is_sent_as_written(self) -> None:
        assert load(answer_notes="It costs $5 and $name.").answer.endswith(
            "It costs $5 and $name."
        )

    def test_brief_comes_after_the_notes(self) -> None:
        noted = load(answer_notes="Note.").briefly()
        assert noted.answer.index("Note.") < noted.answer.index(load().brief)

    def test_bootstrap_hands_the_notes_on(self) -> None:
        from ish import bootstrap
        from ish.settings import Settings

        built = bootstrap.build_prompts(Settings(answer_notes="A.", rerank_notes="R."))
        assert built == load(answer_notes="A.", rerank_notes="R.")


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


def test_the_shipped_answer_keeps_tables_narrow() -> None:
    answer = load().answer
    assert "narrow enough to read in a terminal" in answer
    assert answer.count("\n\n") == 2
    assert load().step.startswith(answer + " ")


class TestBrief:
    def test_brief_follows_each_instruction_that_answers(self) -> None:
        loaded = load()
        brief = loaded.briefly()
        assert brief.answer == f"{loaded.answer}\n\n{loaded.brief}"
        assert brief.answer_cited == f"{loaded.answer_cited}\n\n{loaded.brief}"
        assert brief.step == f"{loaded.step}\n\n{loaded.brief}"
        assert brief.select == loaded.select

    def test_the_shipped_brief_keeps_to_the_summary(self) -> None:
        assert "summary" in load().brief
        assert "Leave sections and gaps empty" in load().brief

    def test_an_own_brief_replaces_the_shipped_one(self, tmp_path: Path) -> None:
        (tmp_path / "brief.md").write_text("One sentence.")
        assert load(tmp_path).briefly().answer.endswith("\n\nOne sentence.")


def test_bootstrap_builds_a_brief_backend_with_brief_instructions(monkeypatch) -> None:
    from ish import bootstrap
    from ish.settings import Settings

    given: list[Prompts] = []

    def stub(model: str, *, prompts: Prompts):
        given.append(prompts)
        return object()

    monkeypatch.setitem(bootstrap.LANGUAGE_MODELS, "stub", stub)
    settings = Settings(
        language_model="stub", answer_model="m", allowed_language_models=("stub",)
    )
    bootstrap.build_language_model(settings)
    bootstrap.build_language_model(settings, brief=True)
    plain, brief = given
    assert plain.answer == load().answer
    assert brief.answer == load().briefly().answer
