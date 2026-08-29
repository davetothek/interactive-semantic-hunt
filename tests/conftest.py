"""Shared test fixtures."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """Keep every test free of the developer's own ish configuration.

    Point the config and cache directories at empty temporary locations
    and drop any ``ISH_*`` variables, so a personal ``ish.toml`` or a
    shell export cannot change a test result.
    """
    base = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    for key in list(os.environ):
        if key.startswith("ISH_"):
            monkeypatch.delenv(key, raising=False)
