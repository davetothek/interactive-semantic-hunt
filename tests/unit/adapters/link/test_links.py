"""Test that a link opens the source of a chunk at its location."""

from pathlib import Path

from ish import bootstrap
from ish.adapters.link.base_url import BaseUrl
from ish.adapters.link.file_uri import FileUri
from ish.application.ports.link import LinkResolver
from ish.domain.chunk import Chunk
from ish.settings import Settings


def _chunk(path: str, start: int = 120, end: int = 164) -> Chunk:
    return Chunk(Path(path), "t", "section", "asciidoc", "s", start, end)


class TestFileUri:
    def test_the_file_uri_carries_the_line_fragment(self) -> None:
        link = FileUri().link(_chunk("/srv/docs/spec/a.adoc"))
        assert link == "file:///srv/docs/spec/a.adoc#L120-164"

    def test_a_space_in_the_path_is_escaped(self) -> None:
        link = FileUri().link(_chunk("/srv/docs/my spec.adoc", 3, 3))
        assert link == "file:///srv/docs/my%20spec.adoc#L3"


class TestBaseUrl:
    def test_the_path_is_taken_from_the_root(self) -> None:
        links = BaseUrl("https://docs.example.com/tree/", Path("/srv/docs"))
        assert (
            links.link(_chunk("/srv/docs/spec/a b.adoc"))
            == "https://docs.example.com/tree/spec/a%20b.adoc#L120-164"
        )

    def test_a_chunk_outside_the_root_links_to_its_file(self) -> None:
        links = BaseUrl("https://docs.example.com", Path("/srv/docs"))
        assert links.link(_chunk("/elsewhere/a.adoc")) == (
            "file:///elsewhere/a.adoc#L120-164"
        )


class TestWiring:
    def test_no_base_links_to_files(self) -> None:
        links = bootstrap.build_link_resolver(Settings(), Path("/srv/docs"))
        assert isinstance(links, FileUri)
        assert isinstance(links, LinkResolver)

    def test_a_base_links_to_it(self) -> None:
        settings = Settings(link_base="https://docs.example.com")
        links = bootstrap.build_link_resolver(settings, Path("/srv/docs"))
        assert isinstance(links, BaseUrl)
        assert links.link(_chunk("/srv/docs/a.adoc")).startswith(
            "https://docs.example.com/a.adoc#"
        )
