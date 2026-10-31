"""Test the Ollama language model against a faked HTTP layer."""

import io
import json
import urllib.error
import urllib.request

import pytest

from ish.adapters.language_model import LANGUAGE_MODELS, _grounded
from ish.adapters.language_model.ollama import OllamaLanguageModel
from ish.application.ports.language_model import LanguageModelError, Source

SOURCES = [Source("1", "/d/a.adoc:1-9 A", "It starts at boot.")]


def _response(payload: dict | bytes):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(data)


class Recorder:
    def __init__(self) -> None:
        self.requests: list = []
        self.reply: dict | bytes = {
            "message": {
                "content": json.dumps(
                    {
                        "covered": True,
                        "summary": {"text": "", "source_ids": []},
                        "sections": [
                            {
                                "heading": "",
                                "style": "prose",
                                "passages": [{"text": "At boot.", "source_ids": ["1"]}],
                                "columns": [],
                                "rows": [],
                            }
                        ],
                        "gaps": [],
                    }
                )
            },
            "prompt_eval_count": 80,
            "eval_count": 9,
        }
        self.error: Exception | None = None

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return _response(self.reply)


@pytest.fixture()
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", rec)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    return rec


def _model(**kwargs) -> OllamaLanguageModel:
    return OllamaLanguageModel("qwen3:8b", timeout=5, **kwargs)


class TestConfiguration:
    def test_the_registry_names_the_backend(self) -> None:
        assert LANGUAGE_MODELS["ollama"] is OllamaLanguageModel

    def test_no_model_is_chosen_for_the_operator(self) -> None:
        with pytest.raises(LanguageModelError, match="answer_model"):
            OllamaLanguageModel("")

    @pytest.mark.parametrize(
        "host", ["localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
    )
    def test_a_local_host_is_accepted(self, host) -> None:
        assert _model(host=host).host.startswith("http://")

    @pytest.mark.parametrize("host", ["http://gpu.example.com:11434", "10.0.0.5:11434"])
    def test_a_host_elsewhere_is_refused(self, host) -> None:
        with pytest.raises(LanguageModelError, match="only sends text to this machine"):
            _model(host=host)

    def test_ollama_host_elsewhere_is_refused(self, monkeypatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "gpu.example.com:11434")
        with pytest.raises(LanguageModelError, match="gpu.example.com"):
            OllamaLanguageModel("qwen3:8b")

    def test_the_default_host_is_local(self, recorder) -> None:
        assert OllamaLanguageModel("qwen3:8b").host == "http://localhost:11434"


class TestAnswer:
    def test_the_request_fixes_the_schema_and_the_prompt(self, recorder) -> None:
        reply = _model().answer("How?", SOURCES)
        request, timeout = recorder.requests[0]
        body = json.loads(request.data)
        assert request.full_url == "http://localhost:11434/api/chat"
        assert timeout == 5
        assert body["format"] == _grounded.SCHEMA
        assert body["stream"] is False
        assert body["messages"][0] == {
            "role": "system",
            "content": _grounded.SYSTEM_PROMPT,
        }
        assert body["messages"][1]["content"] == _grounded.prompt("How?", SOURCES)
        assert reply.covered
        assert reply.passages[0].source_ids == ("1",)
        assert (reply.input_tokens, reply.output_tokens) == (80, 9)

    def test_the_window_holds_a_full_answer_budget(self, recorder) -> None:
        """Ollama drops what does not fit its window, with no signal."""
        from ish.adapters.language_model.ollama import CONTEXT_TOKENS
        from ish.application.answer import DEFAULT_BUDGET_CHARS

        _model().answer("q", SOURCES)
        body = json.loads(recorder.requests[0][0].data)
        assert body["options"]["num_ctx"] == CONTEXT_TOKENS
        # Four characters a token, and room left for the prompt and reply.
        assert CONTEXT_TOKENS > DEFAULT_BUDGET_CHARS // 4 + 4_000

    def test_an_empty_reply_is_an_error(self, recorder) -> None:
        recorder.reply = {}
        with pytest.raises(LanguageModelError, match="did not reply in the JSON"):
            _model().answer("q", SOURCES)


class TestFailures:
    def _fails(self, recorder, error, message) -> None:
        recorder.error = error
        with pytest.raises(LanguageModelError, match=message):
            _model().answer("q", SOURCES)
        assert len(recorder.requests) == 1

    def test_a_timeout(self, recorder) -> None:
        self._fails(recorder, TimeoutError(), "within 5 s")

    def test_a_missing_model_says_how_to_pull_it(self, recorder) -> None:
        error = urllib.error.HTTPError("u", 404, "nf", None, io.BytesIO(b"not found"))
        self._fails(recorder, error, "ollama pull qwen3:8b")

    def test_another_refusal_shows_the_detail(self, recorder) -> None:
        error = urllib.error.HTTPError("u", 500, "e", None, io.BytesIO(b"boom"))
        self._fails(recorder, error, r"\(500\): boom\.$")

    def test_an_unreachable_daemon(self, recorder) -> None:
        self._fails(recorder, urllib.error.URLError("refused"), "refused")

    def test_a_reply_that_is_not_json(self, recorder) -> None:
        recorder.reply = b"<html>"
        with pytest.raises(LanguageModelError, match="not JSON"):
            _model().answer("q", SOURCES)


class TestSelect:
    def test_select_sends_the_select_schema(self, recorder) -> None:
        recorder.reply = {
            "message": {"content": '{"ids": ["1"]}'},
            "prompt_eval_count": 40,
            "eval_count": 3,
        }
        selection = _model().select("q", SOURCES, 5)
        body = json.loads(recorder.requests[0][0].data)
        assert body["format"] == _grounded.SELECT_SCHEMA
        assert body["messages"][0]["content"] == _grounded.SELECT_PROMPT
        assert body["messages"][1]["content"] == _grounded.select_prompt(
            "q", SOURCES, 5
        )
        assert selection.ids == ("1",)
        assert (selection.input_tokens, selection.output_tokens) == (40, 3)


class TestStep:
    def test_step_sends_the_step_schema(self, recorder) -> None:
        recorder.reply = {
            "message": {
                "content": '{"covered": false, "passages": [], "search": ["x"]}'
            }
        }
        reply = _model().step("q", SOURCES, ["done"], 1)
        body = json.loads(recorder.requests[0][0].data)
        assert body["format"] == _grounded.STEP_SCHEMA
        assert body["messages"][0]["content"] == _grounded.STEP_PROMPT
        assert body["messages"][1]["content"] == _grounded.step_prompt(
            "q", SOURCES, ["done"], 1
        )
        assert reply.searches == ("x",)
