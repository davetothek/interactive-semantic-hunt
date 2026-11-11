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


class TestLimit:
    """A user name that fails too often is refused for a time, unasked."""

    def _fail(self, access: Access, user: str = USER, times: int = 1) -> None:
        for _ in range(times):
            with pytest.raises(SignInRefused):
                access.sign_in("directory", user, "wrong-EXAMPLE")

    def test_five_failures_stop_the_sixth_attempt_and_the_method_is_not_asked(
        self, parts
    ) -> None:
        from ish.application.access import MAX_FAILURES
        from ish.application.ports.authenticator import SignInLimited

        access = _access(parts)
        self._fail(access, times=MAX_FAILURES)
        asked = len(parts[0].asked)
        with pytest.raises(SignInLimited) as limited:
            access.sign_in("directory", USER, PASSWORD)
        assert str(limited.value) == (
            "Too many failed sign-ins for reader. Try again in 15 minutes."
        )
        assert len(parts[0].asked) == asked

    def test_the_limit_ends_after_the_window(self, parts) -> None:
        from ish.application.access import WINDOW_SECONDS
        from ish.application.ports.authenticator import SignInLimited

        access = _access(parts)
        self._fail(access, times=5)
        clock = parts[2]
        clock.now += WINDOW_SECONDS - 61
        with pytest.raises(SignInLimited, match="Try again in 2 minutes."):
            access.sign_in("directory", USER, PASSWORD)
        clock.now += 61
        assert access.sign_in("directory", USER, PASSWORD).user == USER

    def test_the_limit_holds_for_each_spelling_of_the_name(self, parts) -> None:
        from ish.application.ports.authenticator import SignInLimited

        access = _access(parts)
        self._fail(access, user="Reader", times=3)
        self._fail(access, user=" reader ", times=2)
        with pytest.raises(SignInLimited):
            access.sign_in("directory", "READER", PASSWORD)

    def test_failures_spread_over_more_than_the_window_set_no_limit(
        self, parts
    ) -> None:
        from ish.application.access import WINDOW_SECONDS

        access = _access(parts)
        for _ in range(8):
            self._fail(access)
            parts[2].now += WINDOW_SECONDS / 4
        assert access.sign_in("directory", USER, PASSWORD).user == USER

    def test_a_success_clears_the_failures(self, parts) -> None:
        access = _access(parts)
        self._fail(access, times=4)
        access.sign_in("directory", USER, PASSWORD)
        self._fail(access, times=4)
        assert access.sign_in("directory", USER, PASSWORD).user == USER

    def test_another_user_is_not_limited(self, parts) -> None:
        access = _access(parts)
        self._fail(access, user="other", times=5)
        assert access.sign_in("directory", USER, PASSWORD).user == USER

    def test_a_directory_that_is_down_is_no_failure_of_the_user(self, parts) -> None:
        access = _access(parts)
        parts[0].down = True
        for _ in range(6):
            with pytest.raises(SignInUnavailable):
                access.sign_in("directory", USER, PASSWORD)
        parts[0].down = False
        assert access.sign_in("directory", USER, PASSWORD).user == USER

    def test_old_names_are_forgotten(self, parts) -> None:
        from ish.application.access import WINDOW_SECONDS

        access = _access(parts)
        self._fail(access, user="first", times=5)
        self._fail(access, user="second")
        parts[2].now += WINDOW_SECONDS
        self._fail(access, user="third")
        assert set(access._failures) == {"third"}
        assert access._limited == {}


class TestManySignInsAtOnce:
    """Guesses sent in parallel reach the method no more often than in turn."""

    def test_only_the_limit_reaches_the_method(self, parts) -> None:
        import threading

        from ish.application.access import MAX_FAILURES
        from ish.application.ports.authenticator import SignInLimited

        release = threading.Event()
        arrived = threading.Semaphore(0)
        reached: list[str] = []

        class Slow:
            def sign_in(self, user, password):
                reached.append(user)
                arrived.release()
                release.wait(5)
                raise SignInRefused(
                    "The directory refused the user name or the password."
                )

        access = Access([], {"directory": Slow()}, parts[1], DAY, parts[2])
        outcomes: list[str] = []

        def guess() -> None:
            try:
                access.sign_in("directory", USER, "wrong-EXAMPLE")
            except SignInRefused:
                outcomes.append("refused")
            except SignInLimited as exc:
                outcomes.append(str(exc))

        threads = [threading.Thread(target=guess) for _ in range(MAX_FAILURES)]
        for thread in threads:
            thread.start()
        for _ in range(MAX_FAILURES):
            assert arrived.acquire(timeout=5)
        with pytest.raises(SignInLimited) as limited:
            access.sign_in("directory", USER, PASSWORD)
        assert str(limited.value) == (
            "Too many sign-ins for reader at once. Try again in a minute."
        )
        release.set()
        for thread in threads:
            thread.join(5)
        assert len(reached) == MAX_FAILURES
        assert outcomes == ["refused"] * MAX_FAILURES
        with pytest.raises(SignInLimited, match="Try again in 15 minutes."):
            access.sign_in("directory", USER, PASSWORD)

    def test_a_sign_in_that_fails_another_way_ends_its_count(self, parts) -> None:
        class Broken:
            def sign_in(self, user, password):
                raise RuntimeError("the method broke")

        access = Access([], {"directory": Broken()}, parts[1], DAY, parts[2])
        for _ in range(8):
            with pytest.raises(RuntimeError):
                access.sign_in("directory", USER, PASSWORD)
        assert access._checking == {}


class TestTheSizeOfASignIn:
    @pytest.mark.parametrize(
        ("user", "password", "error"),
        [
            ("r" * 257, PASSWORD, "A user name has at most 256 characters."),
            (USER, "p" * 1025, "A password has at most 1024 characters."),
        ],
    )
    def test_a_long_name_or_password_asks_the_method_nothing(
        self, user: str, password: str, error: str, parts
    ) -> None:
        with pytest.raises(ValueError, match=error):
            _access(parts).sign_in("directory", user, password)
        assert parts[0].asked == []

    def test_a_guess_on_many_names_reaches_the_method_at_most_a_few_at_once(
        self, parts
    ) -> None:
        import threading

        from ish.application.access import MAX_CHECKING
        from ish.application.ports.authenticator import SignInLimited

        release = threading.Event()
        arrived = threading.Semaphore(0)

        class Slow:
            def sign_in(self, user, password):
                arrived.release()
                release.wait(5)
                raise SignInRefused(
                    "The directory refused the user name or the password."
                )

        access = Access([], {"directory": Slow()}, parts[1], DAY, parts[2])
        threads = [
            threading.Thread(
                target=lambda n=n: pytest.raises(
                    SignInRefused, access.sign_in, "directory", f"user{n}", "x"
                )
            )
            for n in range(MAX_CHECKING)
        ]
        for thread in threads:
            thread.start()
        for _ in range(MAX_CHECKING):
            assert arrived.acquire(timeout=5)
        with pytest.raises(SignInLimited, match="checks many sign-ins now"):
            access.sign_in("directory", "another", "x")
        release.set()
        for thread in threads:
            thread.join(5)
