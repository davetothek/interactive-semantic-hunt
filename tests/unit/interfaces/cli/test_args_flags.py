from ish.interfaces.cli.args import CliArgs


def test_query_and_embedder_flags():
    args = CliArgs.from_args(["auth", "--embedder", "ollama"])
    assert args.query == "auth"
    assert args.settings.embedder == "ollama"


def test_model_flag():
    args = CliArgs.from_args(["auth", "--model", "mxbai-embed-large"])
    assert args.settings.model == "mxbai-embed-large"
