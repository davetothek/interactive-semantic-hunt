"""Test the file where this machine keeps its login tokens."""

import json
import os
import stat

import pytest

from ish.adapters._logins import Saved, TokenFile, TokenFileError

URL = "https://docs.example.invalid/mcp"
SAVED = Saved("login-token-EXAMPLE", "reader", 2_000_000.0)


class TestTokenFile:
    def test_no_file_holds_no_token(self, tmp_path) -> None:
        assert TokenFile(tmp_path / "logins.json").get(URL) is None

    def test_a_token_is_kept_for_its_url(self, tmp_path) -> None:
        tokens = TokenFile(tmp_path / "logins.json")
        tokens.put(URL, SAVED)
        assert tokens.get(URL) == SAVED
        assert tokens.get("https://other.example.invalid/mcp") is None

    def test_two_servers_keep_two_tokens(self, tmp_path) -> None:
        tokens = TokenFile(tmp_path / "logins.json")
        other = "https://other.example.invalid/mcp"
        tokens.put(URL, SAVED)
        tokens.put(other, Saved("other-EXAMPLE", "reader", 1.0))
        assert tokens.get(URL) == SAVED
        assert tokens.get(other) == Saved("other-EXAMPLE", "reader", 1.0)

    def test_only_the_user_can_read_the_file_and_its_directory(self, tmp_path) -> None:
        path = tmp_path / "config" / "ish" / "logins.json"
        TokenFile(path).put(URL, SAVED)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_the_file_holds_no_password(self, tmp_path) -> None:
        path = tmp_path / "logins.json"
        TokenFile(path).put(URL, SAVED)
        assert json.loads(path.read_text()) == {
            URL: {
                "token": "login-token-EXAMPLE",
                "user": "reader",
                "expires": 2_000_000.0,
            }
        }

    @pytest.mark.parametrize("mode", [0o640, 0o604, 0o660])
    def test_a_file_another_user_can_read_is_refused(self, mode: int, tmp_path) -> None:
        path = tmp_path / "logins.json"
        TokenFile(path).put(URL, SAVED)
        os.chmod(path, mode)
        with pytest.raises(TokenFileError) as refused:
            TokenFile(path).get(URL)
        assert str(refused.value) == (
            f"Other users can read the token file {path}. Run: chmod 600 {path}"
        )

    @pytest.mark.parametrize("text", ["not json", "[1, 2]"])
    def test_a_file_that_does_not_parse_is_refused(self, text: str, tmp_path) -> None:
        path = tmp_path / "logins.json"
        path.write_text(text)
        os.chmod(path, 0o600)
        with pytest.raises(TokenFileError, match="Cannot parse the token file"):
            TokenFile(path).get(URL)

    def test_an_entry_that_is_not_a_token_is_none(self, tmp_path) -> None:
        path = tmp_path / "logins.json"
        path.write_text(json.dumps({URL: {"token": "x"}, "other": 3}))
        os.chmod(path, 0o600)
        assert TokenFile(path).get(URL) is None
        assert TokenFile(path).get("other") is None

    def test_remove_deletes_the_token_of_one_url(self, tmp_path) -> None:
        tokens = TokenFile(tmp_path / "logins.json")
        tokens.put(URL, SAVED)
        assert tokens.remove(URL) is True
        assert tokens.get(URL) is None
        assert tokens.remove(URL) is False

    def test_a_part_file_of_a_stopped_write_is_replaced(self, tmp_path) -> None:
        (tmp_path / f".logins.json.{os.getpid()}.part").write_text("half")
        tokens = TokenFile(tmp_path / "logins.json")
        tokens.put(URL, SAVED)
        assert tokens.get(URL) == SAVED


class TestOneServerOneKey:
    @pytest.mark.parametrize(
        "spelling",
        [
            "https://docs.example.invalid/mcp/",
            "HTTPS://Docs.Example.Invalid/mcp",
            "https://DOCS.example.invalid/mcp//",
        ],
    )
    def test_each_spelling_finds_the_token(self, spelling: str, tmp_path) -> None:
        tokens = TokenFile(tmp_path / "logins.json")
        tokens.put(URL, SAVED)
        assert tokens.get(spelling) == SAVED
        assert tokens.remove(spelling) is True

    def test_another_path_is_another_server(self, tmp_path) -> None:
        tokens = TokenFile(tmp_path / "logins.json")
        tokens.put(URL, SAVED)
        assert tokens.get("https://docs.example.invalid/other/mcp") is None
