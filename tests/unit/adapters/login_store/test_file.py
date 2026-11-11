"""Test the file of JSON lines where a server keeps its logins."""

import json
import logging
import stat

from ish.adapters.login_store import LoginFile
from ish.application.ports.login_store import Login, LoginStore

NOW = 1_000_000.0


def _login(digest: str, expires: float = NOW + 100) -> Login:
    return Login(digest, "reader", "directory", expires)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class TestLoginFile:
    def test_it_is_a_login_store(self, tmp_path) -> None:
        assert isinstance(LoginFile(tmp_path / "logins.jsonl"), LoginStore)

    def test_no_file_holds_no_login(self, tmp_path) -> None:
        assert LoginFile(tmp_path / "logins.jsonl").find("a" * 64) is None

    def test_a_login_is_kept_and_found_again(self, tmp_path) -> None:
        path = tmp_path / "logins.jsonl"
        LoginFile(path, Clock()).add(_login("d1"))
        assert LoginFile(path, Clock()).find("d1") == _login("d1")

    def test_only_its_user_can_read_the_file(self, tmp_path) -> None:
        path = tmp_path / "server" / "logins.jsonl"
        LoginFile(path, Clock()).add(_login("d1"))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_one_line_holds_one_login(self, tmp_path) -> None:
        path = tmp_path / "logins.jsonl"
        store = LoginFile(path, Clock())
        store.add(_login("d1"))
        store.add(_login("d2"))
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert lines == [
            {
                "digest": "d1",
                "user": "reader",
                "method": "directory",
                "expires": NOW + 100,
            },
            {
                "digest": "d2",
                "user": "reader",
                "method": "directory",
                "expires": NOW + 100,
            },
        ]

    def test_a_line_the_operator_deletes_admits_nobody(self, tmp_path) -> None:
        path = tmp_path / "logins.jsonl"
        store = LoginFile(path, Clock())
        store.add(_login("d1"))
        store.add(_login("d2"))
        assert store.find("d1") is not None
        kept = [line for line in path.read_text().splitlines() if '"d1"' not in line]
        path.write_text("\n".join(kept) + "\n")
        assert store.find("d1") is None
        assert store.find("d2") is not None

    def test_a_login_that_has_expired_is_dropped_at_the_next_login(
        self, tmp_path
    ) -> None:
        path = tmp_path / "logins.jsonl"
        clock = Clock()
        store = LoginFile(path, clock)
        store.add(_login("old", expires=NOW + 10))
        clock.now += 20
        store.add(_login("new", expires=clock.now + 100))
        assert '"old"' not in path.read_text()
        assert store.find("new") is not None

    def test_a_broken_line_is_skipped_and_named(self, tmp_path, caplog) -> None:
        path = tmp_path / "logins.jsonl"
        good = json.dumps(
            {"digest": "d1", "user": "reader", "method": "directory", "expires": NOW}
        )
        path.write_text(f"not json\n\n{good}\n" + '{"digest": "d2"}\n')
        with caplog.at_level(logging.WARNING):
            assert LoginFile(path).find("d1") is not None
        assert caplog.messages == [
            f"Skip line 1 of {path}: it is not a login.",
            f"Skip line 4 of {path}: it is not a login.",
        ]

    def test_a_part_file_of_a_stopped_write_is_replaced(self, tmp_path) -> None:
        import os

        path = tmp_path / "logins.jsonl"
        (tmp_path / f".logins.jsonl.{os.getpid()}.part").write_text("half")
        LoginFile(path, Clock()).add(_login("d1"))
        assert LoginFile(path).find("d1") is not None
        assert not (tmp_path / f".logins.jsonl.{os.getpid()}.part").exists()
