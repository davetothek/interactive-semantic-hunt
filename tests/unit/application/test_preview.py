"""Test reading the source a chunk points at."""

from pathlib import Path

from ish.application.preview import load_text
from ish.domain.chunk import Chunk


def chunk(path: Path, start: int, end: int, text: str = "") -> Chunk:
    return Chunk(
        path=path,
        text=text,
        kind="function",
        language="python",
        symbol="f",
        start_line=start,
        end_line=end,
    )


class TestReadingFromTheFile:
    """Verify the lines a chunk covers are read back."""

    def test_reads_the_covered_lines(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("one\ntwo\nthree\nfour\n")
        assert load_text(chunk(target, 2, 3)) == "two\nthree\n"

    def test_reads_a_single_line(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_text("only\n")
        assert load_text(chunk(target, 1, 1)) == "only\n"

    def test_shows_the_file_as_it_is_now(self, tmp_path: Path) -> None:
        """A preview must not show what was indexed hours ago."""
        target = tmp_path / "a.py"
        target.write_text("old\n")
        held = chunk(target, 1, 1)
        target.write_text("new\n")
        assert load_text(held) == "new\n"


class TestChunkThatCarriesItsText:
    """Verify a freshly parsed chunk needs no read."""

    def test_its_own_text_is_used(self, tmp_path: Path) -> None:
        absent = tmp_path / "gone.py"
        assert load_text(chunk(absent, 1, 1, text="held\n")) == "held\n"


class TestFailures:
    """Verify a preview explains itself rather than showing nothing."""

    def test_deleted_file(self, tmp_path: Path) -> None:
        message = load_text(chunk(tmp_path / "gone.py", 1, 2))
        assert "no longer exists" in message
        assert "Re-index" in message

    def test_unreadable_file(self, tmp_path: Path) -> None:
        import os

        target = tmp_path / "secret.py"
        target.write_text("x\n")
        os.chmod(target, 0o000)
        try:
            message = load_text(chunk(target, 1, 1))
        finally:
            os.chmod(target, 0o644)
        assert "Cannot read" in message

    def test_file_shorter_than_the_recorded_range(self, tmp_path: Path) -> None:
        """The file shrank since it was indexed."""
        target = tmp_path / "a.py"
        target.write_text("one\n")
        message = load_text(chunk(target, 40, 50))
        assert "has changed" in message

    def test_non_utf8_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.py"
        target.write_bytes(b"# caf\xe9\n")
        assert "Cannot read" in load_text(chunk(target, 1, 1))


class TestReadLines:
    """read_lines gives the real text or nothing, never an explanation."""

    def _chunk(self, path: Path, start: int, end: int, text: str = "") -> Chunk:
        return Chunk(path, text, "section", "asciidoc", "s", start, end)

    def test_the_lines_from_the_file(self, tmp_path: Path) -> None:
        from ish.application.preview import read_lines

        f = tmp_path / "a.adoc"
        f.write_text("one\ntwo\nthree\n")
        assert read_lines(self._chunk(f, 2, 3)) == "two\nthree\n"

    def test_the_text_a_chunk_carries(self, tmp_path: Path) -> None:
        from ish.application.preview import read_lines

        assert read_lines(self._chunk(tmp_path / "x", 1, 1, "kept")) == "kept"

    def test_none_when_it_cannot_be_read(self, tmp_path: Path) -> None:
        from ish.application.preview import read_lines

        f = tmp_path / "a.adoc"
        f.write_text("one\n")
        binary = tmp_path / "b.adoc"
        binary.write_bytes(b"\xff\xfe\xfa")
        assert read_lines(self._chunk(tmp_path / "gone", 1, 1)) is None
        assert read_lines(self._chunk(f, 5, 9)) is None
        assert read_lines(self._chunk(binary, 1, 1)) is None
