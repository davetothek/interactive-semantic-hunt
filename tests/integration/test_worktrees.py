"""Prove that a second checkout costs what it differs by.

Make a real git worktree, index both checkouts with a backend that
counts what it embeds, and assert the second embedded nothing. A fake
git cannot pin this: the claim is that a real worktree reports the same
common directory as the tree it was cut from, and that the files of a
worktree kept under .claude/worktrees/ are indexed with the config that
keeps .claude out of the main index.
"""

import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from ish import bootstrap
from ish.interfaces import heartbeat
from ish.interfaces.python.api import Ish
from ish.settings import Settings


class CountingBackend:
    """Embed by length, and remember every text that was embedded."""

    model_name = "stub"
    embedded: list[str] = []

    @classmethod
    def from_option(cls, model: str, context_tokens=None) -> "CountingBackend":
        return cls()

    def embed_documents(self, texts):
        CountingBackend.embedded.extend(texts)
        return [[float(len(t)), 1.0] for t in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True
    )


@pytest.fixture()
def checkouts(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Settings]:
    """A main checkout with three chunks, and a worktree of it under .claude."""
    monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", CountingBackend)
    CountingBackend.embedded = []

    main = tmp_path / "main"
    (main / "src").mkdir(parents=True)
    (main / "src" / "app.py").write_text(
        "def load():\n    return 1\n\n\ndef save():\n    return 2\n"
    )
    (main / "src" / "util.py").write_text("def helper():\n    return 3\n")
    git(main, "init", "-q")
    git(main, "config", "user.email", "t@example.com")
    git(main, "config", "user.name", "Test")
    git(main, "add", ".")
    git(main, "commit", "-qm", "first")
    worktree = main / ".claude" / "worktrees" / "feature"
    git(main, "worktree", "add", "-q", "-b", "feature", str(worktree))

    settings = replace(
        Settings(),
        cache_dir=str(tmp_path / "idx"),
        git=False,
        ignore=(".git", ".claude"),
    )
    return main, worktree, settings


def index(path: Path, settings: Settings) -> int:
    with Ish(path, settings=settings) as ish:
        return ish.index()


class TestASecondCheckoutCostsWhatItDiffersBy:
    def test_the_worktree_embeds_nothing(self, checkouts) -> None:
        main, worktree, settings = checkouts
        assert index(main, settings) == 3
        after_main = len(CountingBackend.embedded)
        assert after_main == 3

        assert index(worktree, settings) == 3
        assert len(CountingBackend.embedded) == after_main

    def test_an_edit_in_the_worktree_embeds_one_chunk(self, checkouts) -> None:
        """Reuse, not an index that does nothing."""
        main, worktree, settings = checkouts
        index(main, settings)
        index(worktree, settings)
        before = len(CountingBackend.embedded)

        (worktree / "src" / "util.py").write_text("def helper():\n    return 4\n")
        index(worktree, settings)
        assert len(CountingBackend.embedded) == before + 1

    def test_both_indexes_name_one_repository_and_one_store(self, checkouts) -> None:
        main, worktree, settings = checkouts
        index(main, settings)
        index(worktree, settings)
        entries = bootstrap.catalog(settings).entries()
        assert len(entries) == 2
        assert len({entry.repository for entry in entries}) == 1
        assert {entry.checkout for entry in entries} == {
            main.resolve(),
            worktree.resolve(),
        }
        assert len(list((Path(settings.cache_dir)).glob("vectors-*.db"))) == 1

    def test_a_search_of_the_main_tree_shows_each_chunk_once(self, checkouts) -> None:
        main, worktree, settings = checkouts
        index(main, settings)
        index(worktree, settings)
        with Ish(main, settings=settings) as ish:
            results = ish.search("load", limit=10)
        assert results
        assert all(worktree not in chunk.path.parents for chunk, _ in results)
        assert len({chunk.symbol for chunk, _ in results}) == len(results)

    def test_a_search_of_the_worktree_answers_from_it(self, checkouts) -> None:
        main, worktree, settings = checkouts
        index(main, settings)
        index(worktree, settings)
        with Ish(worktree, settings=settings) as ish:
            results = ish.search("load", limit=10)
        assert results
        assert all(worktree in chunk.path.parents for chunk, _ in results)


@pytest.fixture()
def beside(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Settings]:
    """A main checkout and a worktree beside it, where git puts one by default."""
    monkeypatch.setitem(bootstrap.EMBEDDERS, "ollama", CountingBackend)
    CountingBackend.embedded = []

    main = tmp_path / "main"
    (main / "src").mkdir(parents=True)
    (main / "src" / "app.py").write_text(
        "def load():\n    return 1\n\n\ndef save():\n    return 2\n"
    )
    (main / "src" / "util.py").write_text("def helper():\n    return 3\n")
    git(main, "init", "-q")
    git(main, "config", "user.email", "t@example.com")
    git(main, "config", "user.name", "Test")
    git(main, "add", ".")
    git(main, "commit", "-qm", "first")
    worktree = tmp_path / "feature"
    git(main, "worktree", "add", "-q", "-b", "feature", str(worktree))

    settings = replace(Settings(), cache_dir=str(tmp_path / "idx"), git=False)
    return main, worktree, settings


class TestAWorktreeBesideTheCheckout:
    """Verify nothing depends on the worktree sitting inside the main tree.

    Sharing, scoping, and the server are keyed by what git reports of
    the repository, never by where the worktree is on disk.
    """

    def test_the_worktree_embeds_nothing(self, beside) -> None:
        main, worktree, settings = beside
        assert index(main, settings) == 3
        assert index(worktree, settings) == 3
        assert len(CountingBackend.embedded) == 3

    def test_each_search_answers_from_its_own_checkout(self, beside) -> None:
        main, worktree, settings = beside
        index(main, settings)
        index(worktree, settings)
        with Ish(main, settings=settings) as ish:
            found = ish.search("load", limit=10)
            assert found
            assert all(main in chunk.path.parents for chunk, _ in found)
        with Ish(worktree, settings=settings) as ish:
            found = ish.search("load", limit=10)
            assert found
            assert all(worktree in chunk.path.parents for chunk, _ in found)

    def test_one_refresh_visits_both_checkouts_when_asked(self, beside) -> None:
        """This is what lets one server keep a repository current."""
        main, worktree, settings = beside
        index(main, settings)
        index(worktree, settings)
        overrides = {"cache_dir": settings.cache_dir, "git": False}
        alone = bootstrap.refresh_indexes(settings, main, overrides=overrides)
        assert alone == [main.resolve()]
        both = bootstrap.refresh_indexes(
            settings, main, overrides=overrides, checkouts=True
        )
        assert both == sorted([main.resolve(), worktree.resolve()])

    def test_the_worktree_trusts_the_server_of_the_main_tree(self, beside) -> None:
        main, worktree, settings = beside
        repository, _checkout = bootstrap.repository_identity(main)
        heartbeat.write(
            heartbeat.status_for(main),
            heartbeat.Status(
                tree=main.resolve(),
                pid=4242,
                updated=time.time(),
                repository=repository,
                checkout=main.resolve(),
            ),
        )
        with Ish(worktree, settings=settings) as ish:
            kept = ish.kept_by()
        assert kept is not None
        assert kept.tree == main.resolve()
