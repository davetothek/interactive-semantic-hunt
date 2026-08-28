from ish.interfaces.cli.config import CliArgs


def test_query_and_embedder_flags():
    args = CliArgs.from_args(["auth", "--embedder", "ollama"])
    assert args.query == "auth"
    assert args.embedder == "ollama"
