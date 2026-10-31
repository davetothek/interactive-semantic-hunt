"""Test that AsciiDoc includes resolve to the text a reader would see."""

import logging
from pathlib import Path

import pytest

from ish.adapters.parser._asciidoc import MAX_DEPTH, Expander
from ish.adapters.parser.markup import MarkupParser


def _expand(path: Path) -> list[str]:
    return Expander().expand(path, path.read_text().splitlines(keepends=True))


@pytest.fixture()
def spec(tmp_path: Path) -> Path:
    """A specification laid out like the register specifications."""
    (tmp_path / "common" / "detail").mkdir(parents=True)
    (tmp_path / "common" / "attributes.adoc").write_text(
        ":relative: common\n// a comment\n\n:header: tag=header_row\n"
    )
    (tmp_path / "common" / "paths.adoc").write_text(
        ":_REG_A: ./{relative}/detail/regs.adoc\n:REG_A: tag=REG_A\n"
    )
    (tmp_path / "common" / "include.adoc").write_text(
        "include::./attributes.adoc[]\n"
        "include::./paths.adoc[]\n"
        "// tag::header_row[]\n|Name |Reset\n// end::header_row[]\n"
    )
    (tmp_path / "common" / "detail" / "regs.adoc").write_text(
        "// tag::REG_A[]\n|REG_A\n|0x1B7740\n// end::REG_A[]\n"
        "// tag::REG_B[]\n|REG_B\n|0x0\n// end::REG_B[]\n"
    )
    doc = tmp_path / "spec.adoc"
    doc.write_text(
        "include::./common/include.adoc[tags=none]\n"
        "include::./common/attributes.adoc[]\n"
        "include::./common/paths.adoc[]\n"
        "= Spec\n"
        "\n"
        "== Registers\n"
        "|===\n"
        "include::./common/include.adoc[{header}]\n"
        "include::{_REG_A}[{REG_A}]\n"
        "|===\n"
    )
    return doc


class TestExpand:
    def test_an_attribute_names_the_file_and_the_tag(self, spec: Path) -> None:
        expanded = _expand(spec)
        assert expanded[8] == "|REG_A\n|0x1B7740\n"

    def test_a_tag_selects_its_region_and_drops_the_markers(self, spec: Path) -> None:
        assert _expand(spec)[7] == "|Name |Reset\n"

    def test_one_entry_a_line_so_line_numbers_hold(self, spec: Path) -> None:
        lines = spec.read_text().splitlines(keepends=True)
        assert len(_expand(spec)) == len(lines)

    def test_a_file_of_definitions_shows_no_text(self, spec: Path) -> None:
        expanded = _expand(spec)
        assert expanded[1] == "" and expanded[2] == ""

    def test_an_included_file_shows_its_text_without_comments(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "part.adoc").write_text("// note\n:x: 1\nshown {x}\n")
        doc = tmp_path / "d.adoc"
        doc.write_text("include::part.adoc[]\n")
        assert _expand(doc) == ["shown {x}\n"]

    def test_the_file_being_expanded_keeps_its_own_definitions(
        self, tmp_path: Path
    ) -> None:
        doc = tmp_path / "d.adoc"
        doc.write_text(":x: 1\nvalue {x}\n")
        assert _expand(doc) == [":x: 1\n", "value {x}\n"]

    def test_an_unset_attribute_leaves_the_include_as_written(
        self, tmp_path: Path
    ) -> None:
        doc = tmp_path / "d.adoc"
        doc.write_text(":p: a.adoc\n:p!:\ninclude::{p}[]\n")
        assert _expand(doc)[2] == "include::{p}[]\n"

    def test_a_missing_file_leaves_the_include_as_written(
        self, tmp_path: Path, caplog
    ) -> None:
        doc = tmp_path / "d.adoc"
        doc.write_text("include::gone.adoc[]\n")
        with caplog.at_level(logging.DEBUG, logger="ish.adapters.parser._asciidoc"):
            assert _expand(doc) == ["include::gone.adoc[]\n"]
        assert "1 included files could not be read" in caplog.text

    def test_an_unreadable_file_leaves_the_include_as_written(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "bin.adoc").write_bytes(b"\xff\xfe\xfa")
        doc = tmp_path / "d.adoc"
        doc.write_text("include::bin.adoc[]\n")
        assert _expand(doc) == ["include::bin.adoc[]\n"]

    def test_a_path_is_read_from_the_file_that_holds_the_include(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.adoc").write_text("include::b.adoc[]\n")
        (tmp_path / "sub" / "b.adoc").write_text("deep text\n")
        doc = tmp_path / "d.adoc"
        doc.write_text("include::sub/a.adoc[]\n")
        assert _expand(doc) == ["deep text\n"]

    def test_a_file_that_includes_itself_stops(self, tmp_path: Path) -> None:
        loop = tmp_path / "loop.adoc"
        loop.write_text("text\ninclude::loop.adoc[]\n")
        assert _expand(loop) == ["text\n", "include::loop.adoc[]\n"]

    def test_nesting_stops_at_the_depth_limit(self, tmp_path: Path) -> None:
        for n in range(MAX_DEPTH + 2):
            (tmp_path / f"f{n}.adoc").write_text(
                f"level {n}\ninclude::f{n + 1}.adoc[]\n"
            )
        text = _expand(tmp_path / "f0.adoc")[1]
        assert f"level {MAX_DEPTH}" in text
        assert f"level {MAX_DEPTH + 1}" not in text

    def test_several_tags_and_a_negated_one(self, tmp_path: Path) -> None:
        (tmp_path / "r.adoc").write_text(
            "// tag::a[]\nA\n// end::a[]\n"
            "// tag::b[]\nB\n// end::b[]\n"
            "// tag::c[]\nC\n// end::c[]\n"
        )
        doc = tmp_path / "d.adoc"
        doc.write_text('include::r.adoc[tags="a;c;!b"]\n')
        assert _expand(doc) == ["A\nC\n"]

    def test_text_with_no_final_newline_gets_one(self, tmp_path: Path) -> None:
        (tmp_path / "r.adoc").write_text("no newline")
        doc = tmp_path / "d.adoc"
        doc.write_text("include::r.adoc[]\nafter\n")
        assert _expand(doc) == ["no newline\n", "after\n"]

    def test_an_include_whose_target_stays_unresolved(self, tmp_path: Path) -> None:
        doc = tmp_path / "d.adoc"
        doc.write_text("include::{nowhere}/x.adoc[]\n")
        assert _expand(doc) == ["include::{nowhere}/x.adoc[]\n"]


class TestCache:
    """A file of definitions is read once, and its effect follows its inputs."""

    def test_the_cached_effect_matches_a_fresh_one(self, spec: Path) -> None:
        expander = Expander()
        lines = spec.read_text().splitlines(keepends=True)
        assert expander.expand(spec, lines) == expander.expand(spec, lines)

    def test_the_effect_follows_the_attributes_it_reads(self, tmp_path: Path) -> None:
        (tmp_path / "paths.adoc").write_text(":target: {base}/x.adoc\n")
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        (tmp_path / "one" / "x.adoc").write_text("from one\n")
        (tmp_path / "two" / "x.adoc").write_text("from two\n")
        expander = Expander()
        for base in ("one", "two"):
            doc = tmp_path / f"{base}.adoc"
            doc.write_text(
                f":base: {base}\ninclude::paths.adoc[]\ninclude::{{target}}[]\n"
            )
            text = expander.expand(doc, doc.read_text().splitlines(keepends=True))
            assert text[2] == f"from {base}\n"

    def test_a_cached_effect_can_unset(self, tmp_path: Path) -> None:
        (tmp_path / "unset.adoc").write_text(":x!:\n")
        expander = Expander()
        doc = tmp_path / "d.adoc"
        doc.write_text(":x: a.adoc\ninclude::unset.adoc[]\ninclude::{x}[]\n")
        lines = doc.read_text().splitlines(keepends=True)
        expander.expand(doc, lines)
        assert expander.expand(doc, lines)[2] == "include::{x}[]\n"

    def test_a_changed_file_is_read_again(self, tmp_path: Path) -> None:
        import os

        part = tmp_path / "p.adoc"
        part.write_text("old\n")
        doc = tmp_path / "d.adoc"
        doc.write_text("include::p.adoc[]\n")
        expander = Expander()
        lines = doc.read_text().splitlines(keepends=True)
        assert expander.expand(doc, lines) == ["old\n"]
        part.write_text("new\n")
        os.utime(part, (1, 1))
        assert expander.expand(doc, lines) == ["new\n"]


class TestParser:
    """The AsciiDoc parser embeds resolved text, on the lines of the file."""

    def test_a_section_holds_the_rows_it_includes(self, spec: Path) -> None:
        chunks = MarkupParser.asciidoc().parse(spec, spec.read_text())
        registers = next(c for c in chunks if c.symbol == "Spec > Registers")
        assert "|REG_A\n|0x1B7740\n" in registers.text
        assert "|Name |Reset" in registers.text
        assert (registers.start_line, registers.end_line) == (6, 10)

    def test_a_long_section_divides_between_lines_of_the_file(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "big.adoc").write_text("x" * 300 + "\n")
        lines = ["= Doc\n"] + ["include::big.adoc[]\n" for _ in range(10)]
        doc = tmp_path / "d.adoc"
        doc.write_text("".join(lines))
        parser = MarkupParser.asciidoc()
        parser.limit = 1000
        chunks = parser.parse(doc, doc.read_text())
        assert len(chunks) > 1
        assert all(len(c.text) <= 1000 for c in chunks)
        assert chunks[0].start_line == 1 and chunks[-1].end_line == 11
        for left, right in zip(chunks, chunks[1:], strict=False):
            assert right.start_line == left.end_line + 1

    def test_one_line_that_expands_past_the_cap_is_cut(
        self, tmp_path: Path, caplog
    ) -> None:
        (tmp_path / "huge.adoc").write_text("y" * 5000 + "\n")
        doc = tmp_path / "d.adoc"
        doc.write_text("= Doc\ninclude::huge.adoc[]\n")
        parser = MarkupParser.asciidoc()
        parser.limit = 1000
        with caplog.at_level(logging.INFO, logger="ish.adapters.parser.markup"):
            chunks = parser.parse(doc, doc.read_text())
        assert [len(c.text) for c in chunks] == [6, 1000]
        assert (chunks[1].start_line, chunks[1].end_line) == (2, 2)
        assert "1 lines expand past 1000" in caplog.text

    def test_a_file_with_no_heading_resolves_too(self, tmp_path: Path) -> None:
        (tmp_path / "part.adoc").write_text("Included prose sentence.\n")
        doc = tmp_path / "d.adoc"
        doc.write_text("Some prose here.\ninclude::part.adoc[]\n")
        (chunk,) = MarkupParser.asciidoc().parse(doc, doc.read_text())
        assert chunk.text == "Some prose here.\nIncluded prose sentence.\n"

    def test_markdown_divides_nothing_itself(self) -> None:
        assert not hasattr(MarkupParser.markdown(), "limit")

    def test_build_parsers_sets_the_cap_on_asciidoc(self) -> None:
        from ish import bootstrap
        from ish.adapters.parser._limits import chars_for
        from ish.settings import Settings

        settings = Settings(context_tokens=4096)
        built = [
            p for p in bootstrap.build_parsers(settings) if p.language == "asciidoc"
        ]
        inner = built[0]._inner._inner  # CountLimited > SizeLimited > parser
        assert inner.limit == chars_for(4096)
