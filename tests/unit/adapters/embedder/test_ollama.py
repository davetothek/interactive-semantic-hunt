"""Test the Ollama embedder adapter against a stubbed HTTP endpoint."""

import io
import json
import urllib.error
import urllib.request
from collections.abc import Sequence

import pytest

from ish.adapters.embedder import ollama
from ish.adapters.embedder.ollama import DEFAULT_MODEL, OllamaEmbedder


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every wait between attempts instead of taking it."""
    waits: list[float] = []
    monkeypatch.setattr(ollama.time, "sleep", waits.append)
    return waits


class Recorder:
    """Stand in for the daemon, recording every request body."""

    def __init__(self, embeddings_for: object = None) -> None:
        self.requests: list[dict] = []
        self.urls: list[str] = []
        self._embeddings_for = embeddings_for

    def __call__(self, request, timeout=None):  # noqa: ARG002
        self.urls.append(request.full_url)
        body = json.loads(request.data)
        self.requests.append(body)

        if self._embeddings_for is not None:
            payload = {"embeddings": self._embeddings_for}
        else:
            payload = {"embeddings": [[float(len(t))] for t in body["input"]]}
        return _response(payload)


def _response(payload: dict):
    """Build a context-manager response like urlopen returns."""

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(json.dumps(payload).encode())


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", rec)
    return rec


class TestConfiguration:
    """Verify model and host resolution."""

    def test_default_model(self) -> None:
        assert OllamaEmbedder().model_name == DEFAULT_MODEL

    def test_explicit_model(self) -> None:
        assert OllamaEmbedder("mxbai-embed-large").model_name == "mxbai-embed-large"

    def test_the_model_option_names_the_model(self) -> None:
        assert OllamaEmbedder.from_option("mxbai-embed-large").model_name == (
            "mxbai-embed-large"
        )

    def test_an_empty_model_option_means_the_default(self) -> None:
        assert OllamaEmbedder.from_option("").model_name == DEFAULT_MODEL

    def test_default_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        assert OllamaEmbedder().host == "http://localhost:11434"

    def test_host_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:9999")
        assert OllamaEmbedder().host == "http://10.0.0.5:9999"

    def test_explicit_host_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:9999")
        assert OllamaEmbedder(host="http://elsewhere:1234").host == (
            "http://elsewhere:1234"
        )

    def test_trailing_slash_is_dropped(self) -> None:
        assert OllamaEmbedder(host="http://h:1/").host == "http://h:1"

    def test_https_is_kept(self) -> None:
        assert OllamaEmbedder(host="https://h:1").host == "https://h:1"

    def test_construction_makes_no_request(self, recorder: Recorder) -> None:
        """Building the adapter must not touch the network."""
        OllamaEmbedder()
        assert recorder.requests == []


class TestEmbed:
    """Verify the request shape and the returned vectors."""

    def test_empty_input_skips_the_daemon(self, recorder: Recorder) -> None:
        assert OllamaEmbedder().embed_documents([]) == []
        assert recorder.requests == []

    def test_sends_model_and_input(self, recorder: Recorder) -> None:
        OllamaEmbedder("mxbai-embed-large").embed_documents(["a", "bb"])
        assert recorder.requests == [
            {"model": "mxbai-embed-large", "input": ["a", "bb"]}
        ]

    def test_posts_to_the_embed_endpoint(self, recorder: Recorder) -> None:
        OllamaEmbedder(host="http://h:1").embed_documents(["a"])
        assert recorder.urls == ["http://h:1/api/embed"]

    def test_returns_the_vectors(self, recorder: Recorder) -> None:
        assert OllamaEmbedder("all-minilm").embed_documents(["a", "bb"]) == [
            [1.0],
            [2.0],
        ]

    def test_order_is_preserved_across_batches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = Recorder()
        monkeypatch.setattr(urllib.request, "urlopen", rec)
        texts = [f"{'x' * n}" for n in range(1, 6)]

        result = OllamaEmbedder("all-minilm", batch_size=2).embed_documents(texts)

        assert [v[0] for v in result] == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_batches_are_split(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A large index must not go out as one enormous request."""
        rec = Recorder()
        monkeypatch.setattr(urllib.request, "urlopen", rec)

        OllamaEmbedder(batch_size=2).embed_documents(["a", "b", "c", "d", "e"])

        assert [len(r["input"]) for r in rec.requests] == [2, 2, 1]

    def test_batch_size_is_at_least_one(self, recorder: Recorder) -> None:
        OllamaEmbedder(batch_size=0).embed_documents(["a", "b"])
        assert [len(r["input"]) for r in recorder.requests] == [1, 1]

    def test_the_default_batch_keeps_a_request_short(self, recorder: Recorder) -> None:
        """The daemon serves one request at a time.

        A batch is therefore how long a search waits while an index run
        is going, so the default splits rather than sending everything
        as one request nothing can queue ahead of.
        """
        OllamaEmbedder().embed_documents([f"text {n}" for n in range(64)])

        assert len(recorder.requests) > 1
        assert max(len(r["input"]) for r in recorder.requests) <= 8


class TestWaiting:
    """Verify that a query does not wait as long as an index run."""

    class TimingRecorder(Recorder):
        """Record the timeout asked of each request."""

        def __init__(self) -> None:
            super().__init__()
            self.timeouts: list[float] = []

        def __call__(self, request, timeout=None):
            self.timeouts.append(timeout)
            return super().__call__(request, timeout=timeout)

    def _record(self, monkeypatch) -> "TestWaiting.TimingRecorder":
        rec = self.TimingRecorder()
        monkeypatch.setattr(urllib.request, "urlopen", rec)
        return rec

    def test_a_query_waits_less_than_a_stored_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = self._record(monkeypatch)
        embedder = OllamaEmbedder("all-minilm")

        embedder.embed_documents(["stored"])
        embedder.embed_query("asked")

        stored, asked = rec.timeouts
        assert asked < stored

    def test_a_slow_daemon_is_reported_rather_than_waited_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(request, timeout=None):  # noqa: ARG001
            raise TimeoutError("timed out")

        monkeypatch.setattr(urllib.request, "urlopen", boom)

        with pytest.raises(RuntimeError) as exc_info:
            OllamaEmbedder().embed_query("anything")

        message = str(exc_info.value)
        assert "did not answer" in message
        assert "one embedding request at a time" in message


class TestRetry:
    """Verify a batch the daemon did not answer is sent again."""

    class Flaky(Recorder):
        """Fail the first *failures* requests, then answer."""

        def __init__(self, failures: int, error: Exception) -> None:
            super().__init__()
            self._failures = failures
            self._error = error

        def __call__(self, request, timeout=None):
            if len(self.requests) < self._failures:
                self.requests.append(json.loads(request.data))
                raise self._error
            return super().__call__(request, timeout=timeout)

    def _flaky(self, monkeypatch, failures: int, error=None) -> "TestRetry.Flaky":
        rec = self.Flaky(failures, error or TimeoutError("timed out"))
        monkeypatch.setattr(urllib.request, "urlopen", rec)
        return rec

    def test_a_timeout_is_sent_again(self, monkeypatch, caplog, no_sleep) -> None:
        rec = self._flaky(monkeypatch, failures=1)
        with caplog.at_level("WARNING"):
            vectors = OllamaEmbedder("all-minilm").embed_documents(["a"])

        assert vectors == [[1.0]]
        assert len(rec.requests) == 2
        assert no_sleep == [ollama.RETRY_BACKOFF_SECONDS]
        assert "Attempt 2 of 3" in caplog.text

    def test_an_unreachable_daemon_is_sent_again(self, monkeypatch, no_sleep) -> None:
        rec = self._flaky(
            monkeypatch, failures=1, error=urllib.error.URLError("refused")
        )
        assert OllamaEmbedder("all-minilm").embed_documents(["a"]) == [[1.0]]
        assert len(rec.requests) == 2
        assert len(no_sleep) == 1

    def test_the_wait_doubles_and_the_last_failure_is_raised(
        self, monkeypatch, no_sleep
    ) -> None:
        rec = self._flaky(monkeypatch, failures=3)
        with pytest.raises(RuntimeError, match="did not answer"):
            OllamaEmbedder().embed_documents(["a"])

        assert len(rec.requests) == ollama.RETRY_ATTEMPTS
        assert no_sleep == [1.0, 2.0]

    def test_a_query_is_sent_once(self, monkeypatch, no_sleep) -> None:
        """Somebody is waiting on a query, so a second minute is not spent."""
        rec = self._flaky(monkeypatch, failures=1)
        with pytest.raises(RuntimeError, match="did not answer"):
            OllamaEmbedder().embed_query("asked")

        assert len(rec.requests) == 1
        assert no_sleep == []

    def test_a_refused_request_is_not_sent_again(self, monkeypatch, no_sleep) -> None:
        """A missing model will still be missing. Say so at once."""
        error = urllib.error.HTTPError("u", 404, "missing", {}, io.BytesIO(b"no"))
        rec = self._flaky(monkeypatch, failures=1, error=error)
        with pytest.raises(RuntimeError, match="ollama pull"):
            OllamaEmbedder().embed_documents(["a"])

        assert len(rec.requests) == 1
        assert no_sleep == []


class TestFailures:
    """Verify that every failure explains what to do next."""

    def _fail_with(self, monkeypatch, error) -> None:
        def boom(request, timeout=None):  # noqa: ARG001
            raise error

        monkeypatch.setattr(urllib.request, "urlopen", boom)

    def test_daemon_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fail_with(
            monkeypatch, urllib.error.URLError(ConnectionRefusedError("refused"))
        )
        with pytest.raises(RuntimeError) as exc_info:
            OllamaEmbedder().embed_documents(["a"])

        message = str(exc_info.value)
        assert "ollama serve" in message
        assert "--embedder llama.cpp" in message

    def test_daemon_not_running_names_this_machine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        self._fail_with(
            monkeypatch, urllib.error.URLError(ConnectionRefusedError("refused"))
        )
        with pytest.raises(RuntimeError) as exc_info:
            OllamaEmbedder().embed_documents(["a"])

        assert "OLLAMA_HOST" not in str(exc_info.value)

    def test_a_remote_daemon_does_not_advise_a_local_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._fail_with(
            monkeypatch, urllib.error.URLError(ConnectionRefusedError("refused"))
        )
        with pytest.raises(RuntimeError) as exc_info:
            OllamaEmbedder(host="gpu-box:11434").embed_documents(["a"])

        message = str(exc_info.value)
        assert "ollama serve" not in message
        assert "OLLAMA_HOST" in message
        assert "gpu-box" in message

    @pytest.mark.parametrize(
        ("host", "local"),
        [
            ("http://localhost:11434", True),
            ("http://127.0.0.1:11434", True),
            ("http://[::1]:11434", True),
            ("http://gpu-box:11434", False),
            ("http://10.0.0.5:9999", False),
            ("http://:11434", False),
        ],
    )
    def test_a_host_is_read_as_local_or_remote(self, host: str, local: bool) -> None:
        assert ollama._is_loopback(host) is local

    def test_model_missing_says_to_pull_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._fail_with(
            monkeypatch,
            urllib.error.HTTPError(
                "u", 404, "Not Found", {}, io.BytesIO(b'{"error":"model not found"}')
            ),
        )
        with pytest.raises(RuntimeError, match="ollama pull nomic-embed-text"):
            OllamaEmbedder().embed_documents(["a"])

    def test_generation_model_is_not_a_missing_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pulled model that cannot embed must not be reported as absent."""
        self._fail_with(
            monkeypatch,
            urllib.error.HTTPError(
                "u",
                501,
                "Not Implemented",
                {},
                io.BytesIO(b'{"error":"does not support embeddings"}'),
            ),
        )
        with pytest.raises(RuntimeError) as exc_info:
            OllamaEmbedder("qwen2.5-coder:7b").embed_documents(["a"])

        message = str(exc_info.value)
        assert "is an embedding model" in message
        assert "ollama pull" not in message

    def test_reply_is_not_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def garbage(request, timeout=None):  # noqa: ARG001
            return _response_raw(b"<html>not json</html>")

        monkeypatch.setattr(urllib.request, "urlopen", garbage)
        with pytest.raises(RuntimeError, match="not JSON"):
            OllamaEmbedder().embed_documents(["a"])

    def test_wrong_vector_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A generation model returns no embeddings. Say so plainly."""
        rec = Recorder(embeddings_for=[[0.1]])
        monkeypatch.setattr(urllib.request, "urlopen", rec)

        with pytest.raises(RuntimeError, match="is an embedding model"):
            OllamaEmbedder().embed_documents(["a", "b"])

    def test_missing_embeddings_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def empty(request, timeout=None):  # noqa: ARG001
            return _response({})

        monkeypatch.setattr(urllib.request, "urlopen", empty)
        with pytest.raises(RuntimeError, match="returned 0 vectors"):
            OllamaEmbedder().embed_documents(["a"])


def _response_raw(data: bytes):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(data)


def test_satisfies_the_embedder_port() -> None:
    from ish.application.ports.embedder import Embedder

    assert isinstance(OllamaEmbedder(), Embedder)


def test_returns_a_sequence(recorder: Recorder) -> None:
    result: Sequence[Sequence[float]] = OllamaEmbedder("all-minilm").embed_documents(
        ["a"]
    )
    assert list(result) == [[1.0]]


class TestTaskPrefixes:
    """Verify the model's prefixes reach the request body."""

    def test_document_prefix_is_applied(self, recorder: Recorder) -> None:
        OllamaEmbedder("nomic-embed-text").embed_documents(["chunk"])
        assert recorder.requests[0]["input"] == ["search_document: chunk"]

    def test_query_prefix_is_applied(self, recorder: Recorder) -> None:
        OllamaEmbedder("nomic-embed-text").embed_query("find it")
        assert recorder.requests[0]["input"] == ["search_query: find it"]

    def test_model_without_a_convention_is_untouched(self, recorder: Recorder) -> None:
        OllamaEmbedder("all-minilm").embed_documents(["chunk"])
        assert recorder.requests[0]["input"] == ["chunk"]

    def test_query_returns_one_vector(self, recorder: Recorder) -> None:
        assert OllamaEmbedder("all-minilm").embed_query("ab") == [2.0]


class TestContextWindow:
    """Verify the window reaches the daemon, and only when asked for.

    The daemon launches an embedding model with 2048 tokens and drops
    what lies past the window with no signal. nomic-embed-text accepts
    8192, which is about four times the text of each chunk.
    """

    def test_nothing_is_sent_at_the_default(self, recorder: Recorder) -> None:
        OllamaEmbedder().embed_documents(["a"])
        assert "options" not in recorder.requests[0]

    def test_a_window_is_sent_as_num_ctx(self, recorder: Recorder) -> None:
        OllamaEmbedder(context_tokens=8192).embed_documents(["a"])
        assert recorder.requests[0]["options"] == {"num_ctx": 8192}

    def test_a_query_carries_the_window_too(self, recorder: Recorder) -> None:
        OllamaEmbedder(context_tokens=8192).embed_query("q")
        assert recorder.requests[0]["options"] == {"num_ctx": 8192}

    def test_the_option_passes_through(self) -> None:
        made = OllamaEmbedder.from_option("mxbai-embed-large", context_tokens=8192)
        assert made.model_name == "mxbai-embed-large"
        assert made.context_tokens == 8192

    def test_the_default_leaves_the_daemon_to_its_own(self) -> None:
        assert OllamaEmbedder.from_option("").context_tokens is None
