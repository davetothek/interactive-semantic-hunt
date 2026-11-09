"""Test how the Ollama adapters find the daemon they share."""

import pytest

from ish.adapters._ollama import DEFAULT_HOST, is_loopback, normalize_host, resolve_host


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
def test_a_host_is_read_as_local_or_remote(host: str, local: bool) -> None:
    assert is_loopback(host) is local


@pytest.mark.parametrize(
    ("given", "url"),
    [
        ("gpu-box:11434", "http://gpu-box:11434"),
        ("http://gpu-box:11434/", "http://gpu-box:11434"),
        ("https://ollama.example.com", "https://ollama.example.com"),
    ],
)
def test_a_bare_host_becomes_a_url(given: str, url: str) -> None:
    assert normalize_host(given) == url


class TestResolveHost:
    """An option wins, then OLLAMA_HOST, then this machine."""

    def test_an_option_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "env-box:1")
        assert resolve_host("option-box:2") == "http://option-box:2"

    def test_the_variable_comes_next(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "env-box:1")
        assert resolve_host(None) == "http://env-box:1"

    def test_this_machine_is_last(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        assert resolve_host("") == DEFAULT_HOST
