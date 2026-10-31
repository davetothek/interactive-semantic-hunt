"""Test that each kind of location renders, links, and serializes itself."""

from pathlib import Path

import pytest

from ish.domain.chunk import Chunk
from ish.domain.location import (
    CellRange,
    LineRange,
    Location,
    PageRange,
    Section,
    SlideRange,
)


@pytest.mark.parametrize(
    ("location", "rendered", "fragment", "data"),
    [
        (
            LineRange(12, 27),
            ":12-27",
            "L12-27",
            {"kind": "lines", "start": 12, "end": 27},
        ),
        (LineRange(5, 5), ":5-5", "L5", {"kind": "lines", "start": 5, "end": 5}),
        (
            PageRange(4, 5),
            "#page=4-5",
            "page=4",
            {"kind": "page", "start": 4, "end": 5},
        ),
        (PageRange(4, 4), "#page=4", "page=4", {"kind": "page", "start": 4, "end": 4}),
        (
            SlideRange(7, 7),
            "#slide=7",
            "slide=7",
            {"kind": "slide", "start": 7, "end": 7},
        ),
        (
            SlideRange(2, 3),
            "#slide=2-3",
            "slide=2",
            {"kind": "slide", "start": 2, "end": 3},
        ),
        (
            CellRange("Budget", "A2:F40"),
            "#'Budget'!A2:F40",
            "'Budget'!A2:F40",
            {"kind": "cells", "sheet": "Budget", "cells": "A2:F40"},
        ),
        (
            CellRange("Q's", "B1"),
            "#'Q''s'!B1",
            "'Q''s'!B1",
            {"kind": "cells", "sheet": "Q's", "cells": "B1"},
        ),
        (
            Section("2.3", "Installing"),
            "#section=2.3",
            "section=2.3",
            {"kind": "section", "number": "2.3", "title": "Installing"},
        ),
    ],
)
def test_each_kind(location, rendered, fragment, data) -> None:
    assert isinstance(location, Location)
    assert location.render() == rendered
    assert location.fragment() == fragment
    assert location.to_dict() == data


def test_a_chunk_is_located_by_its_lines() -> None:
    chunk = Chunk(Path("/a.py"), "x", "function", "python", "f", 3, 9)
    assert chunk.location == LineRange(3, 9)
