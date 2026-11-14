"""Test the sources use case against a fake search result and a fake reader."""

from pathlib import Path

from ish.application.glossary import Glossary
from ish.application.sources import ChunkText, read_sources
from ish.domain.chunk import Chunk
from ish.domain.match import Match


def make_chunk(name: str, text: str = "") -> Chunk:
    return Chunk(
        path=Path(f"/docs/{name}.adoc"),
        text=text,
        kind="section",
        language="asciidoc",
        symbol=name,
        start_line=1,
        end_line=9,
    )


class TestText:
    def test_each_chunk_keeps_its_rank_its_score_and_its_text(self) -> None:
        first, second = make_chunk("a", "The first."), make_chunk("b", "The second.")
        found = read_sources([Match(first, 0.9), Match(second, 0.4)])
        assert found.chunks == (
            ChunkText(first, 0.9, "The first."),
            ChunkText(second, 0.4, "The second."),
        )

    def test_the_text_comes_from_the_reader(self) -> None:
        stored = make_chunk("a")
        found = read_sources([Match(stored, 1.0)], read=lambda chunk: "As indexed.")
        assert [entry.text for entry in found.chunks] == ["As indexed."]

    def test_a_chunk_that_cannot_be_read_stays_with_empty_text(self) -> None:
        gone, kept = make_chunk("gone"), make_chunk("kept", "Here.")
        found = read_sources(
            [Match(gone, 0.9), Match(kept, 0.8)],
            read=lambda chunk: chunk.text or None,
        )
        assert [(e.chunk, e.text) for e in found.chunks] == [
            (gone, ""),
            (kept, "Here."),
        ]

    def test_no_match_gives_nothing(self) -> None:
        found = read_sources([])
        assert (found.chunks, found.terms) == ((), ())


class TestTerms:
    def test_a_definition_elsewhere_in_the_tree_names_the_acronym(self) -> None:
        glossary = Glossary()
        defining = make_chunk("glossary")
        glossary.add(defining, "| AGC | Automatic Gain Control |")
        doc = make_chunk("spec", "The AGC loop runs.")
        found = read_sources([Match(doc, 1.0)], glossary=glossary)
        (term,) = found.terms
        assert (term.short, term.meaning, term.chunk) == (
            "AGC",
            "Automatic Gain Control",
            defining,
        )

    def test_a_text_that_is_returned_fills_the_glossary(self) -> None:
        glossary = Glossary()
        doc = make_chunk("spec", "The Auto Exposure (AE) block runs first.")
        found = read_sources([Match(doc, 1.0)], glossary=glossary)
        assert [(t.short, t.meaning, t.chunk) for t in found.terms] == [
            ("AE", "Auto Exposure", doc)
        ]
        assert len(glossary) == 1

    def test_an_acronym_no_text_spells_out_is_left_out(self) -> None:
        doc = make_chunk("spec", "The SHT value and the Auto Exposure (AE) block.")
        found = read_sources([Match(doc, 1.0)])
        assert [t.short for t in found.terms] == ["AE"]

    def test_an_acronym_no_returned_text_uses_is_left_out(self) -> None:
        glossary = Glossary()
        glossary.add(make_chunk("glossary"), "| AGC | Automatic Gain Control |")
        found = read_sources(
            [Match(make_chunk("spec", "The loop runs."), 1.0)], glossary=glossary
        )
        assert found.terms == ()

    def test_the_terms_are_sorted_and_each_is_named_once(self) -> None:
        glossary = Glossary()
        glossary.add(
            make_chunk("glossary"),
            "| SHT | Shutter Time |\n| AE | Auto Exposure |",
        )
        found = read_sources(
            [
                Match(make_chunk("one", "SHT then AE."), 0.9),
                Match(make_chunk("two", "AE and SHT again."), 0.8),
            ],
            glossary=glossary,
        )
        assert [t.short for t in found.terms] == ["AE", "SHT"]

    def test_the_meaning_nearest_to_the_returned_chunks_wins(self) -> None:
        glossary = Glossary()
        far = Chunk(Path("/sales/terms.adoc"), "", "section", "asciidoc", None, 1, 2)
        glossary.add(far, "Application Engineer (AE)")
        glossary.add(far, "| AE | Application Engineer |")
        near = make_chunk("glossary")
        glossary.add(near, "| AE | Auto Exposure |")
        found = read_sources(
            [Match(make_chunk("spec", "AE runs first."), 1.0)], glossary=glossary
        )
        assert [(t.meaning, t.chunk) for t in found.terms] == [("Auto Exposure", near)]
