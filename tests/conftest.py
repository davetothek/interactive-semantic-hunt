"""Shared test fixtures."""

import logging
import os

import pytest


@pytest.fixture(autouse=True)
def configured_logging():
    """Send ish log records to stderr for every test.

    A test that asserts on a warning must not depend on some earlier
    test having configured logging first.
    """
    from ish.interfaces.log import setup_logging

    setup_logging(verbosity=0, color=False)
    # A server over HTTP raises the audit logger for the process. Reset it,
    # so a test after one that started a server sees no audit line.
    logging.getLogger("ish.audit").setLevel(logging.NOTSET)


@pytest.fixture(autouse=True, scope="session")
def isolated_session(tmp_path_factory):
    """Keep a fixture of a module out of the directories of the developer.

    A fixture of a module runs before the fixtures of its first test, so
    the isolation of one test comes too late for it. One fixture wrote a
    login file to the data directory of the developer that way. This one
    runs before every other fixture.
    """
    base = tmp_path_factory.mktemp("xdg-session")
    with pytest.MonkeyPatch.context() as patch:
        for name in (
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR",
        ):
            patch.setenv(name, str(base / name.lower()))
        yield


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
    # The index lives under the data directory, so isolate that too or a
    # test writes into the developer's own indexes.
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "data"))
    # The status of a running ish-serve lives under the runtime
    # directory, and a test must never read the developer's server.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(base / "run"))
    for key in list(os.environ):
        if key.startswith("ISH_"):
            monkeypatch.delenv(key, raising=False)
