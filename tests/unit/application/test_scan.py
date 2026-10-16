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
        assert "could not be read" in err


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
        assert "could not be read" in err


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
        assert "could not be parsed" in err


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
        scanner = Scan(parsers=[FakeParser()], root=tree, **kwargs)
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
        assert self._names(tree, include=[r"gen/"]) == ["api_pb2.py"]

    def test_include_accepts_any_of_the_patterns(self, tree: Path) -> None:
        found = self._names(tree, include=[r"gen/", r"app\.py$"])
        assert found == ["api_pb2.py", "app.py"]

    def test_exclude_beats_include(self, tree: Path) -> None:
        """The safer rule wins, so a mistake keeps a file out."""
        found = self._names(tree, include=[r".*\.py$"], exclude=["/vendor/"])
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
        scanner = Scan(parsers=[FakeParser()], exclude=["/vendor/"], root=tree)
        found = set(scanner.discover(tree))
        for path in tree.rglob("*.py"):
            assert scanner.accepts(path) == (path in found), path

    def test_invalid_regex_names_the_option(self) -> None:
        with pytest.raises(ValueError, match="'exclude'"):
            Scan(parsers=[FakeParser()], exclude=["(unclosed"])

    def test_invalid_include_regex(self) -> None:
        with pytest.raises(ValueError, match="'include'"):
            Scan(parsers=[FakeParser()], include=["*bad"])


class TestFiltersJudgeFromTheCheckout:
    """Verify a tree is not rejected for where it sits.

    A worktree lives under .claude/worktrees/, and the config of the
    repository keeps .claude out of the main index. The same config
    serves the worktree, whose own files must still be indexed. The
    patterns are judged from the top of the checkout, so a pattern that
    names a directory of the tree still matches when that tree is the
    one scanned.
    """

    @pytest.fixture()
    def checkout(self, tmp_path: Path) -> Path:
        """A worktree checkout, under the directory the config ignores."""
        top = tmp_path / ".claude" / "worktrees" / "wt"
        src = top / "src"
        (src / "pkg").mkdir(parents=True)
        (src / "app.py").write_text("pass\n")
        (src / "pkg" / "deep.py").write_text("pass\n")
        (top / ".claude").mkdir()
        (top / ".claude" / "hidden.py").write_text("pass\n")
        (src / "vendor").mkdir()
        (src / "vendor" / "lib.py").write_text("pass\n")
        return top

    def _names(self, checkout: Path, root: Path, **kwargs) -> list[str]:
        scanner = Scan(parsers=[FakeParser()], root=root, anchor=checkout, **kwargs)
        return sorted(p.name for p in scanner.discover(root))

    def test_an_ignored_name_above_the_checkout_rejects_nothing(
        self, checkout: Path
    ) -> None:
        found = self._names(checkout, checkout, ignored_dirs=[".claude", "worktrees"])
        assert found == ["app.py", "deep.py", "lib.py"]

    def test_an_exclude_pattern_on_an_ancestor_rejects_nothing(
        self, checkout: Path
    ) -> None:
        found = self._names(checkout, checkout, exclude=[r"(?:^|/)\.claude(?:/|$)"])
        assert found == ["app.py", "deep.py", "lib.py"]

    def test_the_same_pattern_still_matches_inside(self, checkout: Path) -> None:
        found = self._names(checkout, checkout, exclude=["/vendor/"])
        assert found == ["app.py", "deep.py", "hidden.py"]

    def test_a_pattern_naming_the_tree_matches_when_the_tree_is_scanned(
        self, checkout: Path
    ) -> None:
        """The pattern names src/pkg from the top. Scanning src alone must honor it."""
        found = self._names(
            checkout, checkout / "src", exclude=[r"(?:^|/)src/pkg(?:/|$)"]
        )
        assert found == ["app.py", "lib.py"]

    def test_a_pattern_anchored_at_the_start_names_the_top_of_the_checkout(
        self, checkout: Path
    ) -> None:
        found = self._names(checkout, checkout / "src", exclude=[r"^/src/vendor/"])
        assert found == ["app.py", "deep.py"]

    def test_pruning_asks_the_same_question(self, checkout: Path) -> None:
        """Index pruning asks about a stored path. The answers must agree."""
        scanner = Scan(
            parsers=[FakeParser()],
            ignored_dirs=[".claude"],
            exclude=["/vendor/"],
            root=checkout,
            anchor=checkout,
        )
        found = set(scanner.discover(checkout))
        for path in checkout.rglob("*.py"):
            assert scanner.accepts(path) == (path in found), path
        assert scanner.accepts(checkout / "src" / "app.py") is True
        assert scanner.accepts(checkout / ".claude" / "hidden.py") is False

    def test_a_path_outside_the_checkout_is_judged_whole(self, checkout: Path) -> None:
        scanner = Scan(
            parsers=[FakeParser()], ignored_dirs=[".claude"], anchor=checkout
        )
        assert scanner.accepts(checkout.parent / "elsewhere.py") is False

    def test_without_an_anchor_the_whole_path_is_judged(self, checkout: Path) -> None:
        scanner = Scan(parsers=[FakeParser()], ignored_dirs=[".claude"])
        assert scanner.accepts(checkout / "src" / "app.py") is False


class TestIncludeIsAnchoredAtTheRoot:
    """Verify an include pattern names a place in the tree, not a segment.

    One firmware tree kept fourteen worktrees under `.claude/worktrees`,
    each with its own `core`, and `artifacts/core`
    beside them. A pattern matched at any depth admitted 168,763 files,
    about ten times the corpus it named.
    """

    @pytest.fixture()
    def tree(self, tmp_path: Path) -> Path:
        for parent in ("", "artifacts", ".claude/worktrees/wt-1"):
            firmware = tmp_path / parent / "core"
            firmware.mkdir(parents=True)
            (firmware / "main.py").write_text("pass\n")
        return tmp_path

    def _found(self, tree: Path, pattern: str) -> list[str]:
        scanner = Scan(parsers=[FakeParser()], include=[pattern], root=tree)
        return sorted(str(p.relative_to(tree)) for p in scanner.discover(tree))

    def test_a_directory_name_matches_only_at_the_root(self, tree: Path) -> None:
        assert self._found(tree, r"core(?:/|$)") == ["core/main.py"]

    def test_the_old_segment_pattern_also_matches_only_at_the_root(
        self, tree: Path
    ) -> None:
        """`(?:^|/)` still matches, through the `^` branch, and only there."""
        assert self._found(tree, r"(?:^|/)core(?:/|$)") == [
            "core/main.py"
        ]

    def test_any_depth_is_asked_for_explicitly(self, tree: Path) -> None:
        found = self._found(tree, r"(?:.*/)?core/")
        assert found == [
            ".claude/worktrees/wt-1/core/main.py",
            "core/main.py",
            "artifacts/core/main.py",
        ]

    def test_pruning_asks_the_same_question(self, tree: Path) -> None:
        scanner = Scan(parsers=[FakeParser()], include=[r"core/"], root=tree)
        assert scanner.accepts(tree / "core" / "main.py") is True
        assert (
            scanner.accepts(tree / "artifacts" / "core" / "main.py") is False
        )

    def test_a_path_outside_the_root_is_matched_whole(self, tree: Path) -> None:
        scanner = Scan(parsers=[FakeParser()], include=[r".*/other/"], root=tree)
        assert scanner.accepts(Path("/elsewhere/other/x.py")) is True

    def test_a_file_root_is_matched_by_its_name(self, tree: Path) -> None:
        one = tree / "core" / "main.py"
        scanner = Scan(parsers=[FakeParser()], include=[r"main\.py"], root=one)
        assert scanner.discover(one) == [one]

    def test_without_a_root_the_whole_path_is_matched(self, tree: Path) -> None:
        scanner = Scan(parsers=[FakeParser()], include=[r".*/core/"])
        assert scanner.accepts(tree / "artifacts" / "core" / "main.py")


class TestExcludePrunesTheWalk:
    """Verify an excluded directory is never entered.

    `ignore` pruned the walk and `exclude` did not, so every heavy
    directory named in `exclude` was still visited to learn that its
    files were unwanted. One query at the root of a 589,968-file tree
    cost 10 s that way and 1.8 s with the same names in `ignore`.
    """

    @pytest.fixture()
    def tree(self, tmp_path: Path) -> Path:
        (tmp_path / "app.py").write_text("pass\n")
        deep = tmp_path / "build" / "out" / "deep"
        deep.mkdir(parents=True)
        (deep / "gen.py").write_text("pass\n")
        return tmp_path

    def _visited(self, tree: Path, monkeypatch, **kwargs) -> list[str]:
        """Return the directories the walk read, in order."""
        read: list[str] = []
        original = Path.iterdir

        def watched(self_path: Path):
            read.append(str(self_path.relative_to(tree)) if self_path != tree else ".")
            return original(self_path)

        monkeypatch.setattr(Path, "iterdir", watched)
        Scan(parsers=[FakeParser()], root=tree, **kwargs).discover(tree)
        return read

    def test_an_excluded_directory_is_not_entered(self, tree: Path, monkeypatch):
        assert self._visited(tree, monkeypatch, exclude=["/build/"]) == ["."]

    def test_without_the_pattern_the_walk_goes_all_the_way(
        self, tree: Path, monkeypatch
    ) -> None:
        visited = self._visited(tree, monkeypatch)
        assert visited == [".", "build", "build/out", "build/out/deep"]

    def test_a_pattern_on_the_file_alone_prunes_nothing(
        self, tree: Path, monkeypatch
    ) -> None:
        r"""`gen\.py$` names files, so the directories are still read."""
        visited = self._visited(tree, monkeypatch, exclude=[r"gen\.py$"])
        assert "build/out/deep" in visited

    def test_the_files_beneath_are_gone_either_way(self, tree: Path) -> None:
        scanner = Scan(parsers=[FakeParser()], root=tree, exclude=["/build/"])
        assert [p.name for p in scanner.discover(tree)] == ["app.py"]


class TestUnignoreKeepsOneIgnoredTree:
    """Verify one ignored tree can join the index while git filters the rest.

    A repository kept an SVN checkout at `vendor_spec`, hidden by its
    `.gitignore`. Turning git off to reach it also reached a 386 MB
    virtual environment, 903 MB of build output, and a 621 MB cache,
    each found one crash at a time.
    """

    @pytest.fixture()
    def tree(self, tmp_path: Path) -> Path:
        for name in ("vendor_spec", ".venv2", "build"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "mod.py").write_text("pass\n")
        (tmp_path / "app.py").write_text("pass\n")
        return tmp_path

    @staticmethod
    def _git_ignores(path: Path) -> bool:
        """Stand in for git: everything but the tracked file is ignored."""
        return path.name != "app.py"

    def _found(self, tree: Path, **kwargs) -> list[str]:
        scanner = Scan(
            parsers=[FakeParser()], root=tree, ignored_by=self._git_ignores, **kwargs
        )
        return sorted(str(p.relative_to(tree)) for p in scanner.discover(tree))

    def test_git_alone_keeps_every_ignored_tree_out(self, tree: Path) -> None:
        assert self._found(tree) == ["app.py"]

    def test_the_named_tree_joins_and_the_rest_stay_out(self, tree: Path) -> None:
        found = self._found(tree, unignore=[r"vendor_spec/"])
        assert found == ["vendor_spec/mod.py", "app.py"]

    def test_the_pattern_is_anchored_at_the_root(self, tree: Path) -> None:
        nested = tree / "build" / "vendor_spec"
        nested.mkdir()
        (nested / "copy.py").write_text("pass\n")
        found = self._found(tree, unignore=[r"vendor_spec/"])
        assert "build/vendor_spec/copy.py" not in found

    def test_exclude_still_beats_it(self, tree: Path) -> None:
        found = self._found(tree, unignore=[r"vendor_spec/"], exclude=[r"mod\.py$"])
        assert found == ["app.py"]

    def test_pruning_asks_the_same_question(self, tree: Path) -> None:
        scanner = Scan(
            parsers=[FakeParser()],
            root=tree,
            ignored_by=self._git_ignores,
            unignore=[r"vendor_spec/"],
        )
        assert scanner.accepts(tree / "vendor_spec" / "mod.py") is True
        assert scanner.accepts(tree / "build" / "mod.py") is False

    def test_a_malformed_pattern_names_the_option(self) -> None:
        with pytest.raises(ValueError, match="'unignore'"):
            Scan(parsers=[FakeParser()], unignore=["(open"])


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


class TestSkippedFilesAreSummarised:
    """Verify a skipped file is counted, not printed one line each.

    A tree of headers holds many files that declare nothing, and one
    line each buries everything else the run has to say.
    """

    class Grumpy:
        language = "grumpy"
        suffixes = frozenset({".gr"})

        def parse(self, path, source):
            raise ParseError("nothing here")

    def _scan(self, root: Path) -> Scan:
        return Scan(parsers=[self.Grumpy()])

    def test_the_count_is_reported_once(self, tmp_path: Path, caplog) -> None:
        for index in range(5):
            (tmp_path / f"f{index}.gr").write_text("x")
        with caplog.at_level("WARNING"):
            assert self._scan(tmp_path).run(tmp_path) == []
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "5 file(s) could not be parsed" in warnings[0].getMessage()

    def test_the_names_are_kept_for_the_curious(self, tmp_path: Path, caplog) -> None:
        (tmp_path / "one.gr").write_text("x")
        with caplog.at_level("INFO", logger="ish"):
            self._scan(tmp_path).run(tmp_path)
        assert any("one.gr" in r.getMessage() for r in caplog.records)

    def test_nothing_is_said_when_nothing_is_skipped(
        self, tmp_path: Path, caplog
    ) -> None:
        with caplog.at_level("WARNING"):
            self._scan(tmp_path).run(tmp_path)
        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_an_unreadable_file_is_counted_separately(
        self, tmp_path: Path, caplog
    ) -> None:
        bad = tmp_path / "locked.gr"
        bad.write_text("x")
        bad.chmod(0o000)
        try:
            with caplog.at_level("WARNING"):
                self._scan(tmp_path).run(tmp_path)
            said = " ".join(r.getMessage() for r in caplog.records)
            assert "could not be read" in said
        finally:
            bad.chmod(0o600)

    def test_a_second_run_starts_the_count_again(self, tmp_path: Path, caplog) -> None:
        """A count that accumulated would grow with every refresh."""
        (tmp_path / "one.gr").write_text("x")
        scan = self._scan(tmp_path)
        scan.run(tmp_path)
        with caplog.at_level("WARNING"):
            scan.run(tmp_path)
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert "1 file(s)" in warnings[0].getMessage()
