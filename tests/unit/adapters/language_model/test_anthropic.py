"""Test the Anthropic backend against a faked HTTP layer."""

import io
import json
import urllib.error
import urllib.request

import pytest

from ish.adapters.language_model import LANGUAGE_MODELS, _grounded
from ish.adapters.language_model.anthropic import (
    ENDPOINT,
    KEY_VARIABLE,
    NOT_COVERED,
    AnthropicLanguageModel,
)
from ish.application.ports.language_model import LanguageModelError, Source

SOURCES = [
    Source(id="1", title="/docs/a.adoc:1-9 Boot", text="It starts at boot."),
    Source(id="2", title="/docs/b.adoc:3-7 Reset", text="A reset clears it."),
]


def _response(payload: dict):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(json.dumps(payload).encode())


class Recorder:
    def __init__(self) -> None:
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []
        self.reply: dict = {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 900, "output_tokens": 40},
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": "In short, "},
                {
                    "type": "text",
                    "text": "it starts at boot",
                    "citations": [
                        {"type": "char_location", "document_index": 0},
                        {"type": "char_location", "document_index": 0},
                    ],
                },
                {
                    "type": "text",
                    "text": " and a reset clears it.",
                    "citations": [
                        {"type": "char_location", "document_index": 1},
                        {"type": "char_location", "document_index": 9},
                        {"type": "char_location", "document_index": "x"},
                    ],
                },
            ],
        }
        self.error: Exception | None = None

    def __call__(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.error:
            raise self.error
        return _response(self.reply)

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].data)


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", rec)
    monkeypatch.setenv(KEY_VARIABLE, "sk-ant-EXAMPLE")
    return rec


def _model() -> AnthropicLanguageModel:
    return AnthropicLanguageModel("claude-sonnet-5-5", timeout=5)


class TestConfiguration:
    def test_the_registry_names_the_backend(self) -> None:
        assert LANGUAGE_MODELS["anthropic"] is AnthropicLanguageModel

    def test_no_model_is_chosen_for_the_operator(self) -> None:
        with pytest.raises(LanguageModelError, match="answer_model"):
            AnthropicLanguageModel("")

    def test_a_missing_key_sends_nothing(self, recorder, monkeypatch) -> None:
        monkeypatch.delenv(KEY_VARIABLE)
        with pytest.raises(LanguageModelError, match=KEY_VARIABLE):
            _model().answer("q", SOURCES)
        assert recorder.requests == []


class TestRequest:
    def test_the_request_goes_to_the_fixed_endpoint(self, recorder) -> None:
        _model().answer("q", SOURCES)
        request = recorder.requests[0]
        assert request.full_url == ENDPOINT == "https://api.anthropic.com/v1/messages"
        assert request.get_header("X-api-key") == "sk-ant-EXAMPLE"
        assert request.get_header("Anthropic-version") == "2023-06-01"
        assert recorder.timeouts == [5]

    def test_each_source_is_a_cited_document_before_the_question(
        self, recorder
    ) -> None:
        _model().answer("How does it start?", SOURCES)
        body = recorder.body
        assert body["model"] == "claude-sonnet-5-5"
        content = body["messages"][0]["content"]
        assert content[0] == {
            "type": "document",
            "source": {
                "type": "text",
                "media_type": "text/plain",
                "data": "It starts at boot.",
            },
            "title": "/docs/a.adoc:1-9 Boot",
            "citations": {"enabled": True},
        }
        assert content[2] == {"type": "text", "text": "How does it start?"}

    def test_the_system_prompt_forbids_outside_knowledge(self, recorder) -> None:
        _model().answer("q", SOURCES)
        system = recorder.body["system"]
        assert "only the documents" in system
        assert NOT_COVERED in system
        assert "short paragraphs" in system
        assert "output_config" not in recorder.body


class TestReply:
    def test_citations_map_to_source_ids(self, recorder) -> None:
        reply = _model().answer("q", SOURCES)
        assert reply.covered
        assert [(p.text, p.source_ids) for p in reply.passages] == [
            ("In short, ", ()),
            ("it starts at boot", ("1",)),
            (" and a reset clears it.", ("2",)),
        ]
        assert (reply.input_tokens, reply.output_tokens) == (900, 40)

    def test_not_covered_is_read_from_the_reply_word(self, recorder) -> None:
        recorder.reply = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": f"{NOT_COVERED}\n"}],
        }
        reply = _model().answer("q", SOURCES)
        assert not reply.covered
        assert (reply.input_tokens, reply.output_tokens) == (0, 0)

    def test_an_empty_reply_is_not_covered(self, recorder) -> None:
        recorder.reply = {"stop_reason": "end_turn", "content": [{"type": "text"}]}
        assert not _model().answer("q", SOURCES).covered

    @pytest.mark.parametrize(
        ("stop", "message"),
        [("refusal", "declined"), ("max_tokens", "before it finished")],
    )
    def test_an_unfinished_reply_is_an_error(self, recorder, stop, message) -> None:
        recorder.reply = {"stop_reason": stop, "content": []}
        with pytest.raises(LanguageModelError, match=message):
            _model().answer("q", SOURCES)


class TestFailures:
    """A failure is reported. No other model or host is tried."""

    def _fails(self, recorder, error: Exception, message: str) -> None:
        recorder.error = error
        with pytest.raises(LanguageModelError, match=message):
            _model().answer("q", SOURCES)
        assert len(recorder.requests) == 1

    def test_a_timeout(self, recorder) -> None:
        self._fails(recorder, TimeoutError(), "within 5 s")

    def test_a_refused_request_shows_the_detail(self, recorder) -> None:
        error = urllib.error.HTTPError(
            ENDPOINT,
            429,
            "Too Many",
            None,
            io.BytesIO(b"rate limited"),
        )
        self._fails(recorder, error, r"\(429\): rate limited")

    def test_an_unreachable_api(self, recorder) -> None:
        self._fails(recorder, urllib.error.URLError("no route"), "no route")

    def test_a_reply_that_is_not_json(self, recorder, monkeypatch) -> None:
        def garbage(request, timeout):
            recorder.requests.append(request)

            class _Resp(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            return _Resp(b"<html>")

        monkeypatch.setattr(urllib.request, "urlopen", garbage)
        with pytest.raises(LanguageModelError, match="not JSON"):
            _model().answer("q", SOURCES)


class TestSelect:
    def test_select_fixes_the_reply_to_the_schema(self, recorder) -> None:
        recorder.reply = {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 700, "output_tokens": 9},
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": '{"ids": ["2", "1"]}'},
            ],
        }
        selection = _model().select("q", SOURCES, 5)
        body = recorder.body
        assert body["system"] == _grounded.SELECT_PROMPT
        assert body["output_config"] == {
            "format": {"type": "json_schema", "schema": _grounded.SELECT_SCHEMA}
        }
        assert body["messages"][0]["content"] == _grounded.select_prompt(
            "q", SOURCES, 5
        )
        assert selection.ids == ("2", "1")
        assert (selection.input_tokens, selection.output_tokens) == (700, 9)

    @pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
    def test_an_unfinished_choice_is_an_error(self, recorder, stop) -> None:
        recorder.reply = {"stop_reason": stop, "content": []}
        with pytest.raises(LanguageModelError, match=f"did not finish.*{stop}"):
            _model().select("q", SOURCES, 5)

    def test_a_reply_with_no_usage(self, recorder) -> None:
        recorder.reply = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"ids": []}'}],
        }
        assert _model().select("q", SOURCES, 5).ids == ()
