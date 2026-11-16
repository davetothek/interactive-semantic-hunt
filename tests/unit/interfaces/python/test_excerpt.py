"""Test the excerpt of a search result: the parsed text, or the part that matched."""

from pathlib import Path

import pytest

from ish import bootstrap
from ish.domain.chunk import Chunk
from ish.interfaces.python.api import Ish
from ish.settings import Settings

LONG = "".join(f"Sentence {n:02d} of the section says one thing.\n" for n in range(30))


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "rows.adoc").write_text("|REG_A |0x1\n")
    (tmp_path / "spec.adoc").write_text(
        "= Spec\n\n== Registers\n\nThe block has one register.\n\n"
        "|===\ninclude::rows.adoc[]\n|===\n"
    )
    (tmp_path / "guide.md").write_text(f"# Guide\n\n{LONG}")
    return tmp_path


@pytest.fixture()
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each text a vector that names whether it holds sentence 17."""

    class Fake:
        model_name = "fake"

        def embed_documents(self, texts):
            return [
                [1.0 if "Sentence 17" in t and len(t) < 600 else 0.0, 1.0]
                for t in texts
            ]

        def embed_query(self, text):
            return [1.0, 0.0]

    monkeypatch.setattr(bootstrap, "build_embedder", lambda settings: Fake())


def _ish(project: Path, **settings) -> Ish:
    return Ish(project, settings=Settings(no_cache=True, git=False, **settings))


class TestExcerpt:
    def test_an_include_shows_its_text_and_not_the_directive(
        self, project: Path, offline
    ) -> None:
        """The excerpt showed include:: lines where the index held the rows."""
        with _ish(project) as ish:
            spec = next(
                c
                for c, _ in ish.search("register", 10)
                if (c.symbol or "").endswith("Registers")
            )
            text, matched = ish.excerpt(spec, "register")
        assert text is not None and "|REG_A |0x1" in text
        assert "include::" not in text
        assert matched is False

    def test_a_long_section_shows_the_window_that_matched(
        self, project: Path, offline
    ) -> None:
        with _ish(project, doc_windows=True) as ish:
            guide = next(
                c for c, _ in ish.search("one thing", 10) if c.path.suffix == ".md"
            )
            text, matched = ish.excerpt(guide, "which sentence")
        assert matched is True
        assert text is not None and "Sentence 17" in text
        assert "Sentence 00" not in text

    def test_with_no_query_the_whole_text_is_given(
        self, project: Path, offline
    ) -> None:
        with _ish(project, doc_windows=True) as ish:
            guide = next(
                c for c, _ in ish.search("one thing", 10) if c.path.suffix == ".md"
            )
            text, matched = ish.excerpt(guide)
        assert matched is False and text is not None and "Sentence 00" in text

    def test_a_chunk_whose_file_is_gone_gives_none(
        self, project: Path, offline
    ) -> None:
        gone = Chunk(project / "gone.md", "", "section", "markdown", "Gone", 1, 2)
        with _ish(project) as ish:
            assert ish.excerpt(gone, "q") == (None, False)
