"""Test the Scan use case with a fake parser."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from ish.application.ports.parser import ParseError
from ish.application.scan import Scan
from ish.domain.chunk import Chunk


class FakeParser:
    """Return one chunk per file, recording every call for assertions."""

    language = "python"
    suffixes = frozenset({".py"})

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def parse(self, path: Path, source: str) -> Sequence[Chunk]:
        """Record the call and return a single module-level chunk."""
        self.calls.append((path, source))
        return [
            Chunk(
                path=path,
                text=source,
                kind="module",
                language="python",
                symbol=None,
                start_line=1,
                end_line=source.count("\n") or 1,
            )
        ]


@pytest.fixture()
def fake_parser() -> FakeParser:
    return FakeParser()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Create a small project tree for discovery tests.

    Layout::

        root/
        ├── foo.py
        ├── nested/
        │   └── bar.py
        ├── .git/
        │   └── hooks.py       (ignored)
        ├── __pycache__/
        │   └── cached.pyc     (ignored)
        ├── .venv/
        │   └── lib.py          (ignored)
        └── readme.txt          (not Python)
    """
    (tmp_path / "foo.py").write_text("x = 1\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "bar.py").write_text("y = 2\n")

    # Ignored directories.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hooks.py").write_text("z = 3\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.pyc").write_text("")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("a = 4\n")

    # Non-Python file.
    (tmp_path / "readme.txt").write_text("hello\n")

    return tmp_path


class TestRecursiveDiscovery:
    """Verify that Scan finds .py files in nested directories."""

    def test_finds_all_py_files(self, fake_parser: FakeParser, project: Path) -> None:
        scanner = Scan(parsers=[fake_parser])
        scanner.run(project)
        parsed_names = sorted(p.name for p, _ in fake_parser.calls)
        assert parsed_names == ["bar.py", "foo.py"]

    def test_returns_chunks(self, fake_parser: FakeParser, project: Path) -> None:
        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(project)
        assert len(chunks) == 2


class TestIgnoredDirectories:
    """Verify that .git, .venv, venv, and __pycache__ are skipped."""

    def test_git_ignored(self, fake_parser: FakeParser, project: Path) -> None:
        scanner = Scan(parsers=[fake_parser])
        scanner.run(project)
        parsed_paths = [p for p, _ in fake_parser.calls]
        assert not any("hooks.py" in str(p) for p in parsed_paths)

    def test_venv_ignored(self, fake_parser: FakeParser, project: Path) -> None:
        scanner = Scan(parsers=[fake_parser])
        scanner.run(project)
        parsed_paths = [p for p, _ in fake_parser.calls]
        assert not any("lib.py" in str(p) for p in parsed_paths)

    def test_pycache_ignored(self, fake_parser: FakeParser, project: Path) -> None:
        scanner = Scan(parsers=[fake_parser])
        scanner.run(project)
        parsed_paths = [p for p, _ in fake_parser.calls]
        assert not any("cached" in str(p) for p in parsed_paths)


class TestChunkAggregation:
    """Verify that chunks from multiple files are collected together."""

    def test_aggregates_all(self, fake_parser: FakeParser, project: Path) -> None:
        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(project)
        paths = {c.path.name for c in chunks}
        assert paths == {"foo.py", "bar.py"}


class TestSingleFile:
    """Verify that Scan works when given a single file instead of a directory."""

    def test_single_py_file(self, fake_parser: FakeParser, tmp_path: Path) -> None:
        f = tmp_path / "single.py"
        f.write_text("pass\n")
        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(f)
        assert len(chunks) == 1

    def test_single_non_py_file(self, fake_parser: FakeParser, tmp_path: Path) -> None:
        f = tmp_path / "data.txt"
        f.write_text("hello\n")
        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(f)
        assert len(chunks) == 0


class TestUnreadableFile:
    """Verify that unreadable files are skipped with a warning."""

    def test_skips_unreadable_file(
        self,
        fake_parser: FakeParser,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f = tmp_path / "secret.py"
        f.write_text("x = 1\n")
        f.chmod(0o000)

        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(tmp_path)
        # Restore permissions for cleanup.
        f.chmod(0o644)

        assert len(chunks) == 0
        err = capsys.readouterr().err
        assert "Cannot read" in err


class TestNonUtf8File:
    """Verify that a non-UTF-8 file is skipped with a warning."""

    def test_skips_non_utf8_file(
        self,
        fake_parser: FakeParser,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        good = tmp_path / "good.py"
        good.write_text("x = 1\n")
        bad = tmp_path / "latin1.py"
        bad.write_bytes(b"# caf\xe9\n")

        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(tmp_path)

        assert {c.path.name for c in chunks} == {"good.py"}
        err = capsys.readouterr().err
        assert "Cannot read" in err


class TestSyntaxErrorFile:
    """Verify that a parse failure is reported and skipped."""

    class RaisingParser(FakeParser):
        """Raise ``ParseError`` for one file, parse the rest."""

        def parse(self, path: Path, source: str) -> Sequence[Chunk]:
            if path.name == "bad.py":
                raise ParseError("invalid syntax")
            return super().parse(path, source)

    def test_skips_and_reports(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "good.py").write_text("x = 1\n")
        (tmp_path / "bad.py").write_text("def broken(:\n")

        scanner = Scan(parsers=[self.RaisingParser()])
        chunks = scanner.run(tmp_path)

        assert {c.path.name for c in chunks} == {"good.py"}
        err = capsys.readouterr().err
        assert "Cannot parse" in err


class TestParserRouting:
    """Verify that files reach the parser that claims their suffix."""

    class AdocParser(FakeParser):
        """Claim ``.adoc`` files."""

        language = "asciidoc"
        suffixes = frozenset({".adoc"})

    def test_routes_by_suffix(self, tmp_path: Path) -> None:
        py_parser = FakeParser()
        adoc_parser = self.AdocParser()
        (tmp_path / "app.py").write_text("pass\n")
        (tmp_path / "spec.adoc").write_text("= Title\n")
        (tmp_path / "notes.txt").write_text("plain\n")

        scanner = Scan(parsers=[py_parser, adoc_parser])
        chunks = scanner.run(tmp_path)

        assert [p.name for p, _ in py_parser.calls] == ["app.py"]
        assert [p.name for p, _ in adoc_parser.calls] == ["spec.adoc"]
        assert len(chunks) == 2

    def test_duplicate_suffix_names_both_languages(self) -> None:
        """The error must say which parsers clash and how to resolve it."""

        class Header(FakeParser):
            language = "cpp"
            suffixes = frozenset({".py"})

        with pytest.raises(ValueError) as exc_info:
            Scan(parsers=[FakeParser(), Header()])

        message = str(exc_info.value)
        assert "'python'" in message
        assert "'cpp'" in message
        assert "'.py'" in message
        assert "languages" in message


class TestSymlinkedDirectory:
    """Verify that directory symlinks are not followed."""

    def test_ancestor_symlink_no_duplicates(
        self, fake_parser: FakeParser, tmp_path: Path
    ) -> None:
        (tmp_path / "app.py").write_text("pass\n")
        (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)

        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(tmp_path)

        assert len(chunks) == 1

    def test_symlinked_file_still_scanned(
        self, fake_parser: FakeParser, tmp_path: Path
    ) -> None:
        real = tmp_path / "real.py"
        real.write_text("pass\n")
        (tmp_path / "alias.py").symlink_to(real)

        scanner = Scan(parsers=[fake_parser])
        chunks = scanner.run(tmp_path)

        assert len(chunks) == 2


class TestUnreadableDirectory:
    """Verify that unreadable directories are skipped without crashing."""

    def test_skips_unreadable_directory(
        self, fake_parser: FakeParser, tmp_path: Path
    ) -> None:
        (tmp_path / "readable").mkdir()
        (tmp_path / "readable" / "a.py").write_text("pass\n")

        secret = tmp_path / "secret"
        secret.mkdir()
        (secret / "b.py").write_text("pass\n")
        secret.chmod(0o000)

        scanner = Scan(parsers=[fake_parser])
        scanner.run(tmp_path)

        # Restore permissions for cleanup
        secret.chmod(0o755)

        parsed_names = [p.name for p, _ in fake_parser.calls]
        assert "a.py" in parsed_names
        assert "b.py" not in parsed_names


class TestVenvDirectory:
    """Verify that a plain 'venv/' directory is also ignored."""

    def test_venv_ignored(self, fake_parser: FakeParser, tmp_path: Path) -> None:
        (tmp_path / "venv").mkdir()
        (tmp_path / "venv" / "activate.py").write_text("pass\n")
        (tmp_path / "app.py").write_text("pass\n")

        scanner = Scan(parsers=[fake_parser])
        scanner.run(tmp_path)

        parsed_names = [p.name for p, _ in fake_parser.calls]
        assert "activate.py" not in parsed_names
        assert "app.py" in parsed_names


class TestConfigurableIgnores:
    """Verify that the caller chooses which directory names to skip."""

    def test_custom_ignore_list(self, fake_parser: FakeParser, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("pass\n")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "gen.py").write_text("pass\n")

        scanner = Scan(parsers=[fake_parser], ignored_dirs=["build"])
        scanner.run(tmp_path)

        parsed = [p.name for p, _ in fake_parser.calls]
        assert parsed == ["app.py"]

    def test_custom_list_replaces_defaults(
        self, fake_parser: FakeParser, tmp_path: Path
    ) -> None:
        """A caller-supplied list is the whole list, not an addition."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "hook.py").write_text("pass\n")

        scanner = Scan(parsers=[fake_parser], ignored_dirs=["build"])
        scanner.run(tmp_path)

        assert [p.name for p, _ in fake_parser.calls] == ["hook.py"]

    def test_empty_falls_back_to_defaults(
        self, fake_parser: FakeParser, tmp_path: Path
    ) -> None:
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "c.py").write_text("pass\n")
        (tmp_path / "app.py").write_text("pass\n")

        scanner = Scan(parsers=[fake_parser])
        scanner.run(tmp_path)

        assert [p.name for p, _ in fake_parser.calls] == ["app.py"]


class TestPathFilters:
    """Verify the include and exclude regular expressions."""

    @pytest.fixture()
    def tree(self, tmp_path: Path) -> Path:
        (tmp_path / "app.py").write_text("pass\n")
        (tmp_path / "app_test.py").write_text("pass\n")
        vendor = tmp_path / "vendor" / "lib"
        vendor.mkdir(parents=True)
        (vendor / "third.py").write_text("pass\n")
        generated = tmp_path / "gen"
        generated.mkdir()
        (generated / "api_pb2.py").write_text("pass\n")
        return tmp_path

    def _names(self, tree: Path, **kwargs) -> list[str]:
        scanner = Scan(parsers=[FakeParser()], **kwargs)
        return sorted(p.name for p in scanner.discover(tree))

    def test_no_filter_takes_everything(self, tree: Path) -> None:
        assert self._names(tree) == [
            "api_pb2.py",
            "app.py",
            "app_test.py",
            "third.py",
        ]

    def test_exclude_a_directory(self, tree: Path) -> None:
        found = self._names(tree, exclude=["/vendor/"])
        assert "third.py" not in found
        assert "app.py" in found

    def test_exclude_a_suffix_pattern(self, tree: Path) -> None:
        found = self._names(tree, exclude=[r"_pb2\.py$"])
        assert "api_pb2.py" not in found

    def test_several_excludes_all_apply(self, tree: Path) -> None:
        found = self._names(tree, exclude=["/vendor/", r"_test\.py$"])
        assert found == ["api_pb2.py", "app.py"]

    def test_include_restricts_to_matches(self, tree: Path) -> None:
        assert self._names(tree, include=[r"/gen/"]) == ["api_pb2.py"]

    def test_include_accepts_any_of_the_patterns(self, tree: Path) -> None:
        found = self._names(tree, include=[r"/gen/", r"app\.py$"])
        assert found == ["api_pb2.py", "app.py"]

    def test_exclude_beats_include(self, tree: Path) -> None:
        """The safer rule wins, so a mistake keeps a file out."""
        found = self._names(tree, include=[r"\.py$"], exclude=["/vendor/"])
        assert "third.py" not in found

    def test_regex_alternation(self, tree: Path) -> None:
        """A case a glob cannot express."""
        found = self._names(tree, exclude=[r"(_test|_pb2)\.py$"])
        assert found == ["app.py", "third.py"]

    def test_a_single_file_root_is_filtered_too(self, tree: Path) -> None:
        scanner = Scan(parsers=[FakeParser()], exclude=[r"_test\.py$"])
        assert scanner.discover(tree / "app_test.py") == []

    def test_accepts_agrees_with_discovery(self, tree: Path) -> None:
        """Pruning relies on this, so the two must never disagree."""
        scanner = Scan(parsers=[FakeParser()], exclude=["/vendor/"])
        found = set(scanner.discover(tree))
        for path in tree.rglob("*.py"):
            assert scanner.accepts(path) == (path in found), path

    def test_invalid_regex_names_the_option(self) -> None:
        with pytest.raises(ValueError, match="'exclude'"):
            Scan(parsers=[FakeParser()], exclude=["(unclosed"])

    def test_invalid_include_regex(self) -> None:
        with pytest.raises(ValueError, match="'include'"):
            Scan(parsers=[FakeParser()], include=["*bad"])


class TestFilteredPruning:
    """Verify that a newly excluded file leaves the index."""

    def test_excluded_file_is_no_longer_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "keep.py").write_text("pass\n")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "drop.py").write_text("pass\n")

        scanner = Scan(parsers=[FakeParser()], exclude=["/vendor/"])
        assert scanner.accepts(tmp_path / "keep.py") is True
        assert scanner.accepts(vendor / "drop.py") is False
