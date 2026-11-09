"""Test the glossary, which finds what an acronym stands for in the tree.

Every meaning must come from a text of the tree. A shape that only looks
like a definition, such as ``the block (AE)``, must define nothing.
"""

from pathlib import Path

import pytest

from ish.application.glossary import Glossary, definitions, long_form
from ish.domain.chunk import Chunk


def chunk(path: str) -> Chunk:
    return Chunk(Path(path), "t", "section", "asciidoc", None, 1, 2)


class TestLongForm:
    """The letters of the acronym appear in order, the first at a word start."""

    @pytest.mark.parametrize(
        ("short", "text", "meaning"),
        [
            ("AE", "the Auto Exposure", "Auto Exposure"),
            ("AGC", "Automatic Gain Control", "Automatic Gain Control"),
            ("SHT", "the shutter", "shutter"),
            ("R&D", "Research and Development", "Research and Development"),
            ("TGL/S", "toggle sync", "toggle sync"),
        ],
    )
    def test_a_long_form_is_found(self, short: str, text: str, meaning: str) -> None:
        assert long_form(short, text) == meaning

    @pytest.mark.parametrize(
        ("short", "text"),
        [
            ("AE", "the block"),
            ("BIN", "brightness levels"),
            ("AE", "AE"),
            ("HDR", "HDR-RAW mode"),
            ("HDR", "HIGH DYNAMIC RANGE"),
            ("XY", "x"),
            ("ABC", "a | b | c"),
            ("EV", "ev value"),
        ],
    )
    def test_what_does_not_spell_it_out_is_refused(self, short: str, text: str) -> None:
        assert long_form(short, text) is None


class TestDefinitions:
    """A definition is found in each shape a document writes one."""

    @pytest.mark.parametrize(
        ("text", "found"),
        [
            ("The Auto Exposure (AE) block runs.", [("AE", "Auto Exposure")]),
            (
                "Set AGC (Automatic Gain Control) first.",
                [("AGC", "Automatic Gain Control")],
            ),
            ("| SHT | Shutter time |", [("SHT", "Shutter time")]),
            ("| EV\n| Exposure Value\n", [("EV", "Exposure Value")]),
            ("ISP:: Image Signal Processor", [("ISP", "Image Signal Processor")]),
            ("ISP::\nImage Signal Processor", [("ISP", "Image Signal Processor")]),
            ("| FRD | Frame difference. Two frames |", [("FRD", "Frame difference")]),
            ("a *High Dynamic Range* (HDR) mode", [("HDR", "High Dynamic Range")]),
        ],
    )
    def test_each_shape(self, text: str, found: list) -> None:
        assert definitions(text) == found

    @pytest.mark.parametrize(
        "text",
        [
            "the block (AE) runs",
            "see (Figure 3)",
            "AE (shutter value conversion)",
            "| AE | shutter value conversion, analog gain |",
            "| EV\n",
            "| EV\nExposure Value",
            "ISP::",
        ],
    )
    def test_a_shape_that_defines_nothing(self, text: str) -> None:
        assert definitions(text) == []


class TestGlossary:
    """Keep each definition with its chunk, and pick the one nearest."""

    def test_an_acronym_no_text_defines_is_unknown(self) -> None:
        glossary = Glossary()
        glossary.add(chunk("/d/a.adoc"), None)
        glossary.add(chunk("/d/a.adoc"), "")
        assert glossary.lookup("AE") is None
        assert len(glossary) == 0

    def test_a_chunk_counts_once_for_an_acronym(self) -> None:
        glossary = Glossary()
        doc = chunk("/d/a.adoc")
        glossary.add(doc, "Auto Exposure (AE)")
        glossary.add(doc, "Auto Exposure (AE)")
        assert len(glossary) == 1
        assert glossary.lookup("AE").chunk == doc

    def test_the_file_the_answer_read_wins(self) -> None:
        glossary = Glossary()
        far, near = chunk("/x/far.adoc"), chunk("/y/near.adoc")
        glossary.add(far, "Application Engineer (AE)")
        glossary.add(near, "Auto Exposure (AE)")
        assert glossary.lookup("AE", [near]).meaning == "Auto Exposure"

    def test_then_the_same_directory(self) -> None:
        glossary = Glossary()
        far, beside = chunk("/x/far.adoc"), chunk("/y/glossary.adoc")
        glossary.add(far, "Application Engineer (AE)")
        glossary.add(beside, "Auto Exposure (AE)")
        assert glossary.lookup("AE", [chunk("/y/spec.adoc")]).meaning == "Auto Exposure"

    def test_then_the_meaning_the_tree_writes_most(self) -> None:
        glossary = Glossary()
        glossary.add(chunk("/a/1.adoc"), "Application Engineer (AE)")
        glossary.add(chunk("/b/2.adoc"), "Auto Exposure (AE)")
        glossary.add(chunk("/c/3.adoc"), "auto exposure (AE)")
        assert glossary.lookup("AE", [chunk("/z/q.adoc")]).meaning == "Auto Exposure"
