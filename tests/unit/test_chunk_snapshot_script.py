"""Test the chunk snapshot script, which proves what a parser change did.

The script lives outside the package, because it measures ish rather
than ships with it. Load it by path.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "chunk_snapshot.py"


def _load():
    spec = importlib.util.spec_from_file_location("chunk_snapshot", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snap = _load()


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    (root / "guide").mkdir(parents=True)
    (root / "guide" / "a.md").write_text("# Top\n\nintro\n\n## Next\n\ntext\n")
    (root / "tool.py").write_text("def run():\n    return 1\n")
    (root / "broken.py").write_text("def (:\n")
    (root / "notes.txt").write_text("not a language\n")
    (root / ".git").mkdir()
    (root / ".git" / "skipped.md").write_text("# Hidden\n")
    return root


class TestSnapshot:
    """Record each chunk by its place in the tree, in a fixed order."""

    def test_records_name_the_tree_and_the_path_in_it(self, tree: Path) -> None:
        records = list(snap.snapshot([tree]))
        assert {(r["tree"], r["path"]) for r in records} == {
            ("docs", "broken.py"),
            ("docs", "guide/a.md"),
            ("docs", "tool.py"),
        }

    def test_a_chunk_carries_its_lines_and_a_hash_of_its_text(self, tree: Path) -> None:
        (record,) = [r for r in snap.snapshot([tree]) if r["path"] == "tool.py"]
        assert record["kind"] == "function"
        assert record["symbol"] == "run"
        assert record["lines"] == [1, 2]
        assert len(record["text"]) == 16

    def test_a_file_that_raises_is_a_record_with_the_error(self, tree: Path) -> None:
        (record,) = [r for r in snap.snapshot([tree]) if r["path"] == "broken.py"]
        assert record["error"] == "ParseError"

    def test_two_runs_agree(self, tree: Path) -> None:
        assert list(snap.snapshot([tree])) == list(snap.snapshot([tree]))


class TestCompare:
    """Name each record that is in one snapshot and not the other."""

    def test_identical_snapshots_have_no_difference(self, tree: Path) -> None:
        records = list(snap.snapshot([tree]))
        assert snap.compare(records, records) == []

    def test_a_moved_line_shows_on_both_sides(self, tree: Path) -> None:
        before = list(snap.snapshot([tree]))
        (tree / "tool.py").write_text("\ndef run():\n    return 1\n")
        after = list(snap.snapshot([tree]))
        lines = snap.compare(before, after)
        assert [line[0] for line in lines] == ["-", "+"]
        assert '"lines": [1, 2]' in lines[0] and '"lines": [2, 3]' in lines[1]


class TestCommandLine:
    """Write a snapshot, and fail a comparison that finds a difference."""

    def test_out_writes_one_line_per_record(self, tree: Path, tmp_path: Path) -> None:
        out = tmp_path / "before.jsonl"
        assert snap.main(["--out", str(out), str(tree)]) == 0
        lines = out.read_text().splitlines()
        assert len(lines) == len(list(snap.snapshot([tree])))
        assert all(json.loads(line) for line in lines)

    def test_compare_passes_when_nothing_changed(
        self, tree: Path, tmp_path: Path, capsys
    ) -> None:
        out = tmp_path / "s.jsonl"
        snap.main(["--out", str(out), str(tree)])
        assert snap.main(["--compare", str(out), str(out)]) == 0
        assert "identical" in capsys.readouterr().out

    def test_compare_fails_and_shows_the_difference(
        self, tree: Path, tmp_path: Path, capsys
    ) -> None:
        before, after = tmp_path / "before.jsonl", tmp_path / "after.jsonl"
        snap.main(["--out", str(before), str(tree)])
        (tree / "tool.py").write_text("def walk():\n    return 2\n")
        snap.main(["--out", str(after), str(tree)])
        assert snap.main(["--compare", str(before), str(after)]) == 1
        out = capsys.readouterr().out
        assert "2 records differ" in out and '"symbol": "walk"' in out

    def test_a_long_difference_is_cut(
        self, tree: Path, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(snap, "SHOWN", 1)
        before, after = tmp_path / "before.jsonl", tmp_path / "after.jsonl"
        snap.main(["--out", str(before), str(tree)])
        (tree / "tool.py").write_text("def walk():\n    return 2\n")
        snap.main(["--out", str(after), str(tree)])
        snap.main(["--compare", str(before), str(after)])
        assert "... and 1 more" in capsys.readouterr().out

    def test_source_reads_the_package_from_another_checkout(
        self, tree: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(sys, "path", list(sys.path))
        other = tmp_path / "other"
        snap.main(
            ["--out", str(tmp_path / "s.jsonl"), "--source", str(other), str(tree)]
        )
        assert sys.path[0] == str(other.resolve() / "src")

    def test_nothing_to_do_is_an_error(self) -> None:
        with pytest.raises(SystemExit):
            snap.main([])
