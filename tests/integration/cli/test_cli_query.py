from pathlib import Path

import pytest

from ish.interfaces.cli.main import main


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    foo = tmp_path / "foo.py"
    foo.write_text("def foo(): pass\n")
    return tmp_path


def test_query_output(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the CLI correctly wires and outputs a query search."""
    from unittest.mock import MagicMock

    from ish.domain.chunk import Chunk

    # Mock Search to avoid ML dependencies
    mock_search_class = MagicMock()
    mock_search_instance = MagicMock()
    mock_search_class.return_value = mock_search_instance

    c1 = Chunk(
        kind="function",
        language="python",
        symbol="foo",
        path=Path("foo.py"),
        start_line=1,
        end_line=2,
        text="pass",
    )
    mock_search_instance.run.return_value = [(c1, 0.95)]

    monkeypatch.setattr("ish.bootstrap.Search", mock_search_class)

    # Mock the default embedder (llama.cpp)
    monkeypatch.setattr("ish.adapters.embedder.llama_cpp.LlamaCppEmbedder", MagicMock())

    exit_code = main(["my search", str(project)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[0.95] foo.py:1-2  function  foo" in out
    mock_search_instance.run.assert_called_once_with(
        project.resolve(), "my search", limit=5
    )


def test_query_output_ollama(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the CLI correctly wires and outputs using the Ollama embedder."""
    from unittest.mock import MagicMock

    from ish.domain.chunk import Chunk

    # Mock Search to avoid ML dependencies
    mock_search_class = MagicMock()
    mock_search_instance = MagicMock()
    mock_search_class.return_value = mock_search_instance

    c1 = Chunk(
        kind="function",
        language="python",
        symbol="foo",
        path=Path("foo.py"),
        start_line=1,
        end_line=2,
        text="pass",
    )
    mock_search_instance.run.return_value = [(c1, 0.95)]

    monkeypatch.setattr("ish.bootstrap.Search", mock_search_class)

    # Also mock OllamaEmbedder so it doesn't instantiate
    monkeypatch.setattr("ish.adapters.embedder.ollama.OllamaEmbedder", MagicMock())

    exit_code = main(["my search", str(project), "--embedder", "ollama"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[0.95] foo.py:1-2  function  foo" in out
    mock_search_instance.run.assert_called_once_with(
        project.resolve(), "my search", limit=5
    )


def test_query_output_st(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the CLI correctly wires and outputs using the ST embedder."""
    from unittest.mock import MagicMock

    from ish.domain.chunk import Chunk

    mock_search_class = MagicMock()
    mock_search_instance = MagicMock()
    mock_search_class.return_value = mock_search_instance

    c1 = Chunk(
        kind="function",
        language="python",
        symbol="foo",
        path=Path("foo.py"),
        start_line=1,
        end_line=2,
        text="pass",
    )
    mock_search_instance.run.return_value = [(c1, 0.95)]
    monkeypatch.setattr("ish.bootstrap.Search", mock_search_class)

    monkeypatch.setattr(
        "ish.adapters.embedder.sentence_transformer.SentenceTransformerEmbedder",
        MagicMock(),
    )

    exit_code = main(["my search", str(project), "--embedder", "st"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[0.95] foo.py:1-2  function  foo" in out
