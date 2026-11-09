"""Test the one JSON request every HTTP adapter sends."""

import io
import json
import urllib.error
import urllib.request

import pytest

from ish.adapters import _http


class _Reply:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_Reply":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _serve(monkeypatch: pytest.MonkeyPatch, outcome: object) -> list:
    sent: list = []

    def urlopen(request, timeout):
        sent.append((request, timeout))
        if isinstance(outcome, BaseException):
            raise outcome
        return _Reply(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


class TestPostJson:
    """Send JSON, read JSON, and name each way a request fails."""

    def test_the_reply_is_decoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = _serve(monkeypatch, b'{"ok": true}')
        reply = _http.post_json("http://x/api", {"a": 1}, timeout=5, headers={"k": "v"})
        request, timeout = sent[0]
        assert reply == {"ok": True}
        assert json.loads(request.data) == {"a": 1}
        assert request.get_header("Content-type") == "application/json"
        assert request.get_header("K") == "v"
        assert timeout == 5

    def test_a_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _serve(monkeypatch, TimeoutError("slow"))
        with pytest.raises(_http.Timeout) as caught:
            _http.post_json("http://x", {}, timeout=1)
        assert isinstance(caught.value.__cause__, TimeoutError)

    def test_a_refusal_keeps_its_status_and_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        error = urllib.error.HTTPError("u", 404, "nf", None, io.BytesIO(b" gone \n"))
        _serve(monkeypatch, error)
        with pytest.raises(_http.Refused) as caught:
            _http.post_json("http://x", {}, timeout=1)
        assert (caught.value.code, caught.value.detail) == (404, "gone")

    def test_no_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _serve(monkeypatch, urllib.error.URLError("no route"))
        with pytest.raises(_http.Unreachable) as caught:
            _http.post_json("http://x", {}, timeout=1)
        assert caught.value.reason == "no route"

    def test_a_body_that_is_not_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _serve(monkeypatch, b"<html>")
        with pytest.raises(_http.NotJson):
            _http.post_json("http://x", {}, timeout=1)

    def test_every_failure_is_an_http_failure(self) -> None:
        for kind in (_http.Timeout, _http.Refused, _http.Unreachable, _http.NotJson):
            assert issubclass(kind, _http.HttpFailure)
