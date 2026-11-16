"""Test how a long chunk of a document divides into windows."""

from ish.application.windows import WINDOW_CHARS, windows


def lines(count: int, width: int = 90) -> str:
    return "\n".join(f"{n:02d} " + "x" * (width - 3) for n in range(count))


class TestWindows:
    def test_a_short_text_gets_no_window(self) -> None:
        """Its own vector already stands for each of its few sentences."""
        assert windows("a" * (2 * WINDOW_CHARS - 1)) == []

    def test_each_window_holds_whole_lines_up_to_the_size(self) -> None:
        found = windows(lines(20))
        assert len(found) > 1
        for window in found:
            assert all(len(line) == 90 for line in window.splitlines())
            assert len(window.replace("\n", "")) <= WINDOW_CHARS + 90

    def test_each_window_starts_with_the_last_line_of_the_one_before(self) -> None:
        """A sentence that a break divides stands whole in one window."""
        found = windows(lines(20))
        for before, after in zip(found, found[1:], strict=False):
            assert after.splitlines()[0] == before.splitlines()[-1]

    def test_every_line_is_in_a_window(self) -> None:
        text = lines(20)
        held = {line for window in windows(text) for line in window.splitlines()}
        assert held == set(text.splitlines())

    def test_empty_lines_are_left_out(self) -> None:
        text = "\n\n".join(lines(20).splitlines())
        assert all("" not in w.splitlines() for w in windows(text))

    def test_a_long_line_is_a_window_by_itself_and_not_repeated(self) -> None:
        long = "y" * (WINDOW_CHARS + 50)
        text = "\n".join(["short one", long, "short two", "z" * 300, "w" * 300])
        found = windows(text)
        assert long in found
        assert sum(window.count(long) for window in found) == 1

    def test_the_last_window_is_not_the_overlap_alone(self) -> None:
        found = windows(lines(9, width=100))
        assert len(found[-1].splitlines()) > 1 or len(found) == 1


class TestTablesGetNoWindows:
    """A table names values. Windows of its rows made a file of registers
    dozens of vectors, and 290 such files took 156 of 240 result places.
    """

    def test_an_asciidoc_table_gets_no_window(self) -> None:
        rows = "".join(
            f"|REG_{n:03d}\n|u1.0\n|A setting of the block {n}\n" for n in range(60)
        )
        assert windows(rows) == []

    def test_a_markdown_table_gets_no_window(self) -> None:
        rows = "".join(
            f"| reg{n} | 0x{n:02x} | a setting of the block |\n" for n in range(40)
        )
        assert windows(rows) == []

    def test_prose_with_a_few_rows_keeps_its_windows(self) -> None:
        prose = "".join(
            f"Sentence {n} of the section says one thing.\n" for n in range(30)
        )
        assert windows(prose + "|A |B\n|C |D\n")
