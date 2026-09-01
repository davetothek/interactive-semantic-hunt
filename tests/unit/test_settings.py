"""Test settings resolution and the CLI/TOML option parity guarantee."""

import argparse
from dataclasses import fields

import pytest

from ish.interfaces.cli.args import add_settings_options
from ish.settings import (
    CONFIG_FILENAME,
    ConfigError,
    Settings,
    find_project_config,
    load_settings,
    option_names,
)


def _write(path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestOptionParity:
    """Verify that the CLI and ish.toml accept the same option set."""

    def test_every_option_has_a_cli_flag(self) -> None:
        parser = argparse.ArgumentParser()
        add_settings_options(parser)
        destinations = {a.dest for a in parser._actions}
        for name in option_names():
            assert name in destinations, f"{name} is missing a CLI flag"

    def test_every_cli_flag_is_a_settings_field(self) -> None:
        parser = argparse.ArgumentParser()
        add_settings_options(parser)
        names = set(option_names())
        for action in parser._actions:
            if action.dest == "help":
                continue
            assert action.dest in names, f"{action.dest} is not a setting"

    def test_every_option_is_accepted_from_toml(self, tmp_path, monkeypatch) -> None:
        """Confirm no field is silently rejected by the config loader."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        body = "\n".join(
            f"{f.name} = {_toml_literal(f.default)}" for f in fields(Settings)
        )
        _write(tmp_path / CONFIG_FILENAME, body)

        settings = load_settings(start=tmp_path, environ={})
        assert settings == Settings()


def _toml_literal(value) -> str:
    if isinstance(value, tuple):
        return "[" + ", ".join(f'"{v}"' for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


class TestPrecedence:
    """Verify that later sources override earlier ones."""

    def test_defaults_when_nothing_is_configured(self, tmp_path) -> None:
        assert load_settings(start=tmp_path, environ={}) == Settings()

    def test_project_config_overrides_user_config(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        _write(tmp_path / "cfg" / "ish" / CONFIG_FILENAME, "limit = 11\nmodel = 'u'\n")
        project = tmp_path / "proj"
        _write(project / CONFIG_FILENAME, "limit = 22\n")

        settings = load_settings(start=project, environ={})
        assert settings.limit == 22
        # An option the project file omits keeps the user-level value.
        assert settings.model == "u"

    def test_env_overrides_config(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, "limit = 22\n")
        settings = load_settings(start=tmp_path, environ={"ISH_LIMIT": "33"})
        assert settings.limit == 33

    def test_cli_overrides_env(self, tmp_path) -> None:
        settings = load_settings(
            {"limit": 44}, start=tmp_path, environ={"ISH_LIMIT": "33"}
        )
        assert settings.limit == 44

    def test_none_override_does_not_win(self, tmp_path) -> None:
        """A flag the user did not pass must not clobber a config value."""
        _write(tmp_path / CONFIG_FILENAME, "limit = 22\n")
        settings = load_settings({"limit": None}, start=tmp_path, environ={})
        assert settings.limit == 22


class TestProjectDiscovery:
    """Verify that a project config is found by walking upward."""

    def test_finds_config_in_parent(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, "limit = 7\n")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_project_config(nested) == tmp_path / CONFIG_FILENAME

    def test_returns_none_when_absent(self, tmp_path) -> None:
        assert find_project_config(tmp_path) is None


class TestCoercion:
    """Verify that values are converted to the type of each field."""

    def test_int_from_string(self, tmp_path) -> None:
        settings = load_settings(start=tmp_path, environ={"ISH_LIMIT": "9"})
        assert settings.limit == 9

    def test_tuple_from_toml_list(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, 'ignore = ["a", "b"]\n')
        assert load_settings(start=tmp_path, environ={}).ignore == ("a", "b")

    def test_tuple_from_comma_separated_env(self, tmp_path) -> None:
        settings = load_settings(start=tmp_path, environ={"ISH_IGNORE": "a,b,c"})
        assert settings.ignore == ("a", "b", "c")

    def test_invalid_int_is_reported(self, tmp_path) -> None:
        with pytest.raises(ConfigError, match="limit"):
            load_settings(start=tmp_path, environ={"ISH_LIMIT": "many"})

    def test_invalid_list_is_reported(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, "ignore = 5\n")
        with pytest.raises(ConfigError, match="ignore"):
            load_settings(start=tmp_path, environ={})


class TestBadConfig:
    """Verify that config problems surface instead of being swallowed."""

    def test_malformed_toml_is_reported(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, "limit = = 3\n")
        with pytest.raises(ConfigError, match="Cannot parse"):
            load_settings(start=tmp_path, environ={})

    def test_unknown_key_warns_and_continues(self, tmp_path, capsys, caplog) -> None:
        _write(tmp_path / CONFIG_FILENAME, "limit = 3\nnot_an_option = 1\n")
        with caplog.at_level("WARNING"):
            settings = load_settings(start=tmp_path, environ={})
        assert settings.limit == 3
        assert "not_an_option" in caplog.text

    def test_unknown_env_var_warns(self, tmp_path, caplog) -> None:
        with caplog.at_level("WARNING"):
            load_settings(start=tmp_path, environ={"ISH_NOPE": "1"})
        assert "nope" in caplog.text


class TestBoolCoercion:
    """Verify the boolean rule, since ``bool`` is a subclass of ``int``.

    No option is boolean today. This pins the contract so the first one
    added does not fall through to the integer branch.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("true", True), ("YES", True), ("1", True), ("off", False), ("", False)],
    )
    def test_string_to_bool(self, value, expected) -> None:
        from ish.settings import _coerce

        assert _coerce("flag", value, False) is expected

    def test_non_string_to_bool(self) -> None:
        from ish.settings import _coerce

        assert _coerce("flag", 1, False) is True


class TestUnreadableConfig:
    """Verify that an unreadable config file is reported, not skipped."""

    def test_unreadable_user_config(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        (tmp_path / "cfg" / "ish" / CONFIG_FILENAME).mkdir(parents=True)

        with pytest.raises(ConfigError, match="Cannot read"):
            load_settings(start=tmp_path, environ={})

    def test_project_config_must_be_a_file(self, tmp_path) -> None:
        """A directory of that name is not a config file, so ignore it."""
        (tmp_path / CONFIG_FILENAME).mkdir()
        assert find_project_config(tmp_path) is None
        assert load_settings(start=tmp_path, environ={}) == Settings()
