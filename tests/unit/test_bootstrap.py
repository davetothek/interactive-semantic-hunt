"""Test the composition root."""

from ish import bootstrap
from ish.application.ports.parser import Parser
from ish.application.scan import Scan


def test_default_embedder_is_registered() -> None:
    assert bootstrap.DEFAULT_EMBEDDER in bootstrap.EMBEDDERS


def test_build_parsers_satisfy_the_port() -> None:
    parsers = bootstrap.build_parsers()
    assert parsers
    assert all(isinstance(p, Parser) for p in parsers)


def test_build_scan_wires_the_use_case() -> None:
    assert isinstance(bootstrap.build_scan(), Scan)
