"""Integration test: drive the bash completion through a real bash."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "contrib" / "shell" / "ish.bash"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    return tmp_path


def replies(line: str, project: Path, wordbreaks: str = ":") -> list[str]:
    """Return what bash would offer for *line*, with the cursor at its end.

    Put the interpreter's own bin directory first on PATH, so the
    `ish-complete` that answers is the one this checkout installed.
    """
    env = dict(os.environ)
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["COMP_LINE"] = line
    env["COMP_POINT"] = str(len(line))
    # Bash sets COMP_WORDBREAKS itself at start, so assign it in the script.
    script = (
        f"source {SCRIPT}\n"
        f"COMP_WORDBREAKS='{wordbreaks}'\n"
        "_ish\n"
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    done = subprocess.run(
        ["bash", "-c", script],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return [reply for reply in done.stdout.split("\n") if reply]


class TestBashCompletion:
    """Verify the shell finishes a filter word the way the picker does."""

    def test_a_key_is_finished(self, project: Path) -> None:
        assert replies("ish ty", project) == ["type:"]

    def test_a_value_is_finished_past_the_colon(self, project: Path) -> None:
        """Readline replaces only what follows a colon, so offer only that."""
        assert replies("ish lang:cp", project) == ["cpp"]

    def test_the_whole_word_is_offered_when_a_colon_does_not_break(
        self, project: Path
    ) -> None:
        assert replies("ish lang:cp", project, wordbreaks=" ") == ["lang:cpp"]

    def test_several_answers_are_listed(self, project: Path) -> None:
        assert replies("ish type:c", project) == ["code", "config"]

    def test_a_subtree_is_offered(self, project: Path) -> None:
        assert replies("ish under:/s", project) == ["/src/"]

    def test_a_word_that_fits_nothing_completes_to_itself(self, project: Path) -> None:
        assert replies("ish exposure", project) == ["exposure"]

    def test_only_the_last_word_is_read(self, project: Path) -> None:
        assert replies("ish state machine ty", project) == ["type:"]

    def test_a_flag_is_finished(self, project: Path) -> None:
        assert "--limit" in replies("ish --li", project)
