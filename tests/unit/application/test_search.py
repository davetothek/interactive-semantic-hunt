"""Test the Search orchestration use case."""

from pathlib import Path
from unittest.mock import MagicMock

from ish.application.search import Search
from ish.domain.chunk import Chunk


class TestSearchUseCase:
    """Verify the orchestration logic of the Search use case."""

    def _make_chunk(self, symbol: str) -> Chunk:
        return Chunk(
            kind="function",
            symbol=symbol,
            path=Path("foo.py"),
            start_line=1,
            end_line=2,
            text="def foo(): pass",
        )

    def test_search_orchestration(self, monkeypatch) -> None:
        """Confirm it calls all ports in the correct sequence."""
        mock_parser = MagicMock()
        mock_embedder = MagicMock()
        mock_store = MagicMock()

        # We also want to mock Scan so we don't hit the filesystem
        mock_scan_class = MagicMock()
        mock_scan_instance = MagicMock()
        mock_scan_class.return_value = mock_scan_instance
        monkeypatch.setattr("ish.application.search.Scan", mock_scan_class)

        # Setup mock returns
        c1 = self._make_chunk("foo")
        mock_scan_instance.run.return_value = [c1]

        # embed() is called twice: once for the chunk, once for the query
        mock_embedder.embed.side_effect = [
            [[0.1, 0.2]],  # Return for the chunk
            [[0.9, 0.9]],  # Return for the query
        ]

        mock_store.search.return_value = [(c1, 0.85)]

        # Run the use case
        search = Search(
            parsers=[mock_parser],
            embedder=mock_embedder,
            vector_store=mock_store,
        )
        results = search.run(Path("dummy"), "my query", limit=3)

        # Verify Scan was initialized with the parser and run with the path
        mock_scan_class.assert_called_once_with(parsers=[mock_parser])
        mock_scan_instance.run.assert_called_once_with(Path("dummy"))

        # Verify the chunk was formatted and embedded
        assert mock_embedder.embed.call_count == 2
        mock_embedder.embed.assert_any_call(["function foo:\ndef foo(): pass"])
        mock_embedder.embed.assert_any_call(["my query"])

        # Verify the store was populated and queried
        mock_store.add.assert_called_once_with([c1], [[0.1, 0.2]])
        mock_store.search.assert_called_once_with([0.9, 0.9], limit=3)

        # Verify the final result
        assert results == [(c1, 0.85)]

    def test_search_no_chunks(self, monkeypatch) -> None:
        """Confirm it aborts early if no chunks are found."""
        mock_parser = MagicMock()
        mock_embedder = MagicMock()
        mock_store = MagicMock()

        mock_scan_class = MagicMock()
        mock_scan_instance = MagicMock()
        mock_scan_class.return_value = mock_scan_instance
        monkeypatch.setattr("ish.application.search.Scan", mock_scan_class)

        # Scanner returns empty list
        mock_scan_instance.run.return_value = []

        search = Search(
            parsers=[mock_parser],
            embedder=mock_embedder,
            vector_store=mock_store,
        )
        results = search.run(Path("dummy"), "my query")

        assert results == []
        mock_embedder.embed.assert_not_called()
        mock_store.add.assert_not_called()

    def test_search_query_embed_fails(self, monkeypatch) -> None:
        """Confirm it handles when the query fails to embed (returns empty)."""
        mock_parser = MagicMock()
        mock_embedder = MagicMock()
        mock_store = MagicMock()

        mock_scan_class = MagicMock()
        mock_scan_instance = MagicMock()
        mock_scan_class.return_value = mock_scan_instance
        monkeypatch.setattr("ish.application.search.Scan", mock_scan_class)

        mock_scan_instance.run.return_value = [self._make_chunk("foo")]

        # embed() returns vectors for the chunk, but empty for the query
        mock_embedder.embed.side_effect = [[[0.1, 0.2]], []]

        search = Search(
            parsers=[mock_parser],
            embedder=mock_embedder,
            vector_store=mock_store,
        )
        results = search.run(Path("dummy"), "my query")

        assert results == []
        mock_store.search.assert_not_called()
