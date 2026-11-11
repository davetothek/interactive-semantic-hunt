"""Test what admits the requests of a server, and signs users in."""

import hashlib

import pytest

from ish.application.access import TOKEN_BYTES, Access, digest_of
from ish.application.ports.authenticator import (
    Identity,
    SignInRefused,
    SignInUnavailable,
)
from ish.application.ports.login_store import Login

STATIC = "test-token-EXAMPLE"
USER = "reader"
PASSWORD = "hunter2-EXAMPLE"
DAY = 86_400.0


class Static:
    def admit(self, bearer):
        return Identity("token", "token") if bearer == STATIC else None


class Directory:
    """A stand-in for a directory: one user with one password."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.down = False

    def sign_in(self, user, password):
        self.asked.append(user)
        if self.down:
            raise SignInUnavailable("The directory cannot be reached.")
        if (user, password) != (USER, PASSWORD):
            raise SignInRefused("The directory refused the user name or the password.")
        return Identity(user, "directory")


class Store:
    """A login store in memory, which the test reads."""

    def __init__(self) -> None:
        self.logins: dict[str, Login] = {}

    def add(self, login):
        self.logins[login.digest] = login

    def find(self, digest):
        return self.logins.get(digest)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def parts():
    return Directory(), Store(), Clock()


def _access(parts) -> Access:
    directory, store, clock = parts
    return Access([Static()], {"directory": directory}, store, DAY, clock)


class TestAdmit:
    def test_the_static_token_is_admitted(self, parts) -> None:
        assert _access(parts).admit(STATIC) == Identity("token", "token")

    @pytest.mark.parametrize("bearer", ["", "wrong-token-EXAMPLE"])
    def test_another_token_is_refused(self, bearer: str, parts) -> None:
        assert _access(parts).admit(bearer) is None

    def test_a_login_token_is_admitted_until_it_expires(self, parts) -> None:
        access = _access(parts)
        granted = access.sign_in("directory", USER, PASSWORD)
        assert access.admit(granted.token) == Identity(USER, "directory")
        clock = parts[2]
        clock.now += DAY - 1
        assert access.admit(granted.token) is not None
        clock.now += 1
        assert access.admit(granted.token) is None

    def test_a_login_the_operator_deleted_is_refused(self, parts) -> None:
        access = _access(parts)
        granted = access.sign_in("directory", USER, PASSWORD)
        parts[1].logins.clear()
        assert access.admit(granted.token) is None


class TestSignIn:
    def test_the_store_keeps_the_digest_and_never_the_token(self, parts) -> None:
        granted = _access(parts).sign_in("directory", USER, PASSWORD)
        (login,) = parts[1].logins.values()
        assert login == Login(
            digest_of(granted.token), USER, "directory", granted.expires
        )
        assert granted.token not in repr(parts[1].logins)
        assert login.digest == hashlib.sha256(granted.token.encode()).hexdigest()

    def test_the_token_lasts_the_lifetime(self, parts) -> None:
        granted = _access(parts).sign_in("directory", USER, PASSWORD)
        assert granted.expires == parts[2].now + DAY
        assert granted.user == USER

    def test_each_token_is_new_and_long(self, parts) -> None:
        access = _access(parts)
        tokens = {access.sign_in("directory", USER, PASSWORD).token for _ in range(5)}
        assert len(tokens) == 5
        # 32 random bytes in base64 for URLs are 43 characters.
        assert all(len(token) >= TOKEN_BYTES * 4 // 3 for token in tokens)

    @pytest.mark.parametrize(
        ("user", "password", "error"),
        [
            (USER, "", "Give a password."),
            ("", PASSWORD, "Give a user name."),
            (" ", "x", "Give a user name."),
        ],
    )
    def test_an_empty_name_or_password_asks_the_method_nothing(
        self, user: str, password: str, error: str, parts
    ) -> None:
        with pytest.raises(ValueError, match=error):
            _access(parts).sign_in("directory", user, password)
        assert parts[0].asked == []
        assert parts[1].logins == {}

    def test_a_method_the_server_does_not_offer(self, parts) -> None:
        with pytest.raises(ValueError) as refused:
            _access(parts).sign_in("magic", USER, PASSWORD)
        assert str(refused.value) == (
            "This server offers no sign-in method 'magic'. It offers: directory."
        )

    def test_a_server_with_no_method_says_none(self, parts) -> None:
        access = Access([Static()], {}, parts[1], DAY, parts[2])
        assert access.methods() == []
        with pytest.raises(ValueError, match="It offers: none."):
            access.sign_in("directory", USER, PASSWORD)

    def test_a_refusal_gives_no_token(self, parts) -> None:
        with pytest.raises(SignInRefused):
            _access(parts).sign_in("directory", USER, "wrong-EXAMPLE")
        assert parts[1].logins == {}

    def test_a_directory_that_is_down_gives_no_token(self, parts) -> None:
        parts[0].down = True
        with pytest.raises(SignInUnavailable):
            _access(parts).sign_in("directory", USER, PASSWORD)
        assert parts[1].logins == {}

    def test_the_methods_keep_their_order(self, parts) -> None:
        directory, store, clock = parts
        access = Access([], {"b": directory, "a": directory}, store, DAY, clock)
        assert access.methods() == ["b", "a"]
