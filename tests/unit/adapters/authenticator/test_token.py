"""Test the method that admits the static token of the operator."""

import pytest

from ish.adapters.authenticator import AUTHENTICATORS
from ish.adapters.authenticator.token import USER, StaticToken
from ish.application.ports.authenticator import Authenticator, Identity

TOKEN = "test-token-EXAMPLE"
VARIABLE = "ISH_MCP_TOKEN"


class TestStaticToken:
    def test_it_is_an_authenticator(self) -> None:
        assert isinstance(StaticToken(TOKEN, VARIABLE), Authenticator)

    def test_the_token_is_admitted(self) -> None:
        assert StaticToken(TOKEN, VARIABLE).admit(TOKEN) == Identity(USER, "token")

    @pytest.mark.parametrize(
        "bearer",
        ["", "wrong-token-EXAMPLE", TOKEN[:-1], TOKEN + "x", f" {TOKEN}", "tökén"],
    )
    def test_another_token_is_refused(self, bearer: str) -> None:
        assert StaticToken(TOKEN, VARIABLE).admit(bearer) is None

    def test_no_token_refuses_to_start_and_names_the_variable(self) -> None:
        with pytest.raises(ValueError, match=f"Set {VARIABLE} before serving MCP"):
            StaticToken("", VARIABLE)

    def test_it_reads_its_two_options(self) -> None:
        options = {"token": TOKEN, "token_variable": VARIABLE, "other": "x"}
        assert StaticToken.configured(options).admit(TOKEN) is not None

    def test_the_registry_names_it_token(self) -> None:
        assert AUTHENTICATORS["token"] == StaticToken.configured

    def test_the_comparison_takes_the_same_time_for_any_token(
        self, monkeypatch
    ) -> None:
        # A comparison that stops at the first wrong byte tells a client
        # how much of a guess was right. `compare_digest` does not stop.
        import hmac

        compared: list[tuple[bytes, bytes]] = []
        original = hmac.compare_digest

        def recorded(given, kept):
            compared.append((given, kept))
            return original(given, kept)

        monkeypatch.setattr(hmac, "compare_digest", recorded)
        method = StaticToken(TOKEN, VARIABLE)
        assert method.admit("wrong-token-EXAMPLE") is None
        assert method.admit(TOKEN) is not None
        assert compared == [
            (b"wrong-token-EXAMPLE", TOKEN.encode()),
            (TOKEN.encode(), TOKEN.encode()),
        ]
