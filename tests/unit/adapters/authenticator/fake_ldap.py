"""A stand-in for python-ldap, which no test of this suite installs.

It keeps a directory in memory: users with their passwords, and the
members of groups. Each connection records what the adapter did, so a
test can show what reached the directory.
"""

import sys
import types
from dataclasses import dataclass, field

import pytest


class LDAPError(Exception):
    pass


# The names python-ldap gives its errors.
class INVALID_CREDENTIALS(LDAPError):
    pass


class SERVER_DOWN(LDAPError):
    pass


class TIMEOUT(LDAPError):
    pass


class CONNECT_ERROR(LDAPError):
    pass


def escape_dn_chars(value: str) -> str:
    """Escape *value* for a distinguished name, as RFC 4514 asks."""
    escaped = "".join(f"\\{c}" if c in '\\,+"<>;=' else c for c in value)
    if escaped.startswith(("#", " ")):
        escaped = "\\" + escaped
    if escaped.endswith(" "):
        escaped = escaped[:-1] + "\\ "
    return escaped.replace("\x00", "\\00")


def escape_filter_chars(value: str) -> str:
    """Escape *value* for a search filter, as RFC 4515 asks."""
    table = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\x00": r"\00"}
    return "".join(table.get(c, c) for c in value)


@dataclass
class Directory:
    """What the directory holds, and what each connection did."""

    users: dict[str, str] = field(default_factory=dict)
    groups: dict[str, set[str]] = field(default_factory=dict)
    down: bool = False
    failure: Exception | None = None
    binds: list[tuple[str, str]] = field(default_factory=list)
    searches: list[tuple[str, int, str]] = field(default_factory=list)
    options: dict[int, object] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)
    tls_started: int = 0
    unbound: int = 0
    unbind_fails: bool = False


class Connection:
    def __init__(self, directory: Directory) -> None:
        self._directory = directory

    def set_option(self, option: int, value: object) -> None:
        self._directory.options[option] = value

    def start_tls_s(self) -> None:
        self._directory.tls_started += 1

    def simple_bind_s(self, dn: str, password: str) -> None:
        directory = self._directory
        directory.binds.append((dn, password))
        if directory.down:
            raise SERVER_DOWN({"desc": "Can't contact LDAP server"})
        if directory.failure is not None:
            raise directory.failure
        if directory.users.get(dn) != password:
            raise INVALID_CREDENTIALS({"desc": "Invalid credentials"})

    def search_s(self, base: str, scope: int, query: str, attributes: list[str]):
        self._directory.searches.append((base, scope, query))
        members = self._directory.groups.get(base, set())
        return [(base, {})] if any(f"={m})" in query for m in members) else []

    def unbind_s(self) -> None:
        self._directory.unbound += 1
        if self._directory.unbind_fails:
            raise LDAPError({"desc": "closed"})


def module(directory: Directory) -> types.ModuleType:
    """Return a module that answers as python-ldap, for *directory*."""
    made = types.ModuleType("ldap")
    for name, value in {
        "OPT_REFERRALS": 1,
        "OPT_NETWORK_TIMEOUT": 2,
        "OPT_TIMEOUT": 3,
        "OPT_X_TLS_REQUIRE_CERT": 4,
        "OPT_X_TLS_DEMAND": 5,
        "OPT_X_TLS_NEWCTX": 6,
        "SCOPE_BASE": 0,
        "LDAPError": LDAPError,
        "INVALID_CREDENTIALS": INVALID_CREDENTIALS,
        "SERVER_DOWN": SERVER_DOWN,
        "TIMEOUT": TIMEOUT,
        "CONNECT_ERROR": CONNECT_ERROR,
    }.items():
        setattr(made, name, value)

    def initialize(url: str) -> Connection:
        directory.urls.append(url)
        return Connection(directory)

    made.initialize = initialize
    return made


def install(monkeypatch: pytest.MonkeyPatch, directory: Directory) -> None:
    """Put the stand-in in place of python-ldap for one test.

    As in python-ldap, the two escape functions live in modules of their
    own, which `import ldap` does not make attributes of `ldap`. An
    adapter that reaches them through `ldap.filter` fails here as it
    would on a real install.
    """
    dn = types.ModuleType("ldap.dn")
    dn.escape_dn_chars = escape_dn_chars
    found = types.ModuleType("ldap.filter")
    found.escape_filter_chars = escape_filter_chars
    monkeypatch.setitem(sys.modules, "ldap", module(directory))
    monkeypatch.setitem(sys.modules, "ldap.dn", dn)
    monkeypatch.setitem(sys.modules, "ldap.filter", found)
