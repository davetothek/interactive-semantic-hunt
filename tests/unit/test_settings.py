"""Test settings resolution and the CLI/TOML option parity guarantee."""

from dataclasses import fields

import pytest

from ish.interfaces.cli.args import build_parser
from ish.settings import (
    CONFIG_BASENAME,
    CONFIG_DIRNAME,
    CONFIG_ENV_VAR,
    CONFIG_FILENAME,
    CONFIG_OPTION,
    ConfigError,
    Settings,
    find_project_config,
    load_settings,
    option_names,
    project_configs,
)

# The command line carries a few names that are not options: the two
# positionals, the interactive flag, help, and version. The config path
# joins them, and it is the one exemption worth stating. A file cannot
# name where to find itself, so that name is a flag and an environment
# variable but never a key inside a file.
NOT_OPTIONS = {"help", "version", "query", "path", "interactive", CONFIG_OPTION}


def _write(path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestOptionParity:
    """Verify that the CLI and ish.toml accept the same option set."""

    def test_every_option_has_a_cli_flag(self) -> None:
        destinations = {a.dest for a in build_parser()._actions}
        for name in option_names():
            assert name in destinations, f"{name} is missing a CLI flag"

    def test_every_cli_flag_is_a_settings_field(self) -> None:
        names = set(option_names())
        for action in build_parser()._actions:
            if action.dest in NOT_OPTIONS:
                continue
            assert action.dest in names, f"{action.dest} is not a setting"

    def test_the_config_path_is_the_one_exemption(self) -> None:
        """Hold the exemption to one name, so another cannot join it quietly."""
        assert CONFIG_OPTION not in option_names()
        assert CONFIG_OPTION in {a.dest for a in build_parser()._actions}

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
        """A file that cannot be read is an error, not an absence."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        config = tmp_path / "cfg" / "ish" / CONFIG_BASENAME
        config.parent.mkdir(parents=True)
        config.write_text("limit = 3\n")
        config.chmod(0o000)
        try:
            with pytest.raises(ConfigError, match="Cannot read"):
                load_settings(start=tmp_path, environ={})
        finally:
            config.chmod(0o600)

    def test_a_directory_of_that_name_is_not_a_config(
        self, tmp_path, monkeypatch
    ) -> None:
        """Skip it, the way a project config of that shape is skipped."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        (tmp_path / "cfg" / "ish" / CONFIG_BASENAME).mkdir(parents=True)
        assert load_settings(start=tmp_path, environ={}) == Settings()

    def test_project_config_must_be_a_file(self, tmp_path) -> None:
        """A directory of that name is not a config file, so ignore it."""
        (tmp_path / CONFIG_FILENAME).mkdir()
        assert find_project_config(tmp_path) is None
        assert load_settings(start=tmp_path, environ={}) == Settings()


class TestConfigInheritance:
    """Verify that a config beside a subtree adds to the one above it.

    Reading only the nearest file would make a setting for one tree
    silently drop what the repository above had already decided.
    """

    def _write(self, directory, body: str) -> None:
        (directory / CONFIG_DIRNAME).mkdir(parents=True, exist_ok=True)
        (directory / CONFIG_DIRNAME / CONFIG_BASENAME).write_text(body)

    def test_a_subtree_inherits_what_it_does_not_name(self, tmp_path) -> None:
        child = tmp_path / "child"
        child.mkdir()
        self._write(tmp_path, "limit = 11\nmodel = 'up'\n")
        self._write(child, "limit = 22\n")

        settings = load_settings(start=child, environ={})
        assert settings.limit == 22
        assert settings.model == "up"

    def test_the_nearest_file_wins(self, tmp_path) -> None:
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        self._write(tmp_path, "limit = 1\n")
        self._write(tmp_path / "a", "limit = 2\n")
        assert load_settings(start=deep, environ={}).limit == 2

    def test_the_chain_is_outermost_first(self, tmp_path) -> None:
        child = tmp_path / "child"
        child.mkdir()
        self._write(tmp_path, "limit = 1\n")
        self._write(child, "limit = 2\n")
        chain = project_configs(child)
        assert [c.parent.parent.name for c in chain] == [tmp_path.name, "child"]

    def test_find_project_config_still_returns_the_nearest(self, tmp_path) -> None:
        child = tmp_path / "child"
        child.mkdir()
        self._write(tmp_path, "limit = 1\n")
        self._write(child, "limit = 2\n")
        assert find_project_config(child) == (child / CONFIG_DIRNAME / CONFIG_BASENAME)

    def test_no_config_anywhere_is_an_empty_chain(self, tmp_path) -> None:
        assert project_configs(tmp_path / "nowhere") == []


class TestNamedConfigFile:
    """Verify that a caller can name the config file to read.

    The name stands in place of the upward search. The user file below
    it still applies, so a machine-wide preference survives a project
    file chosen for one run.
    """

    def test_the_named_file_is_read(self, tmp_path) -> None:
        named = _write(tmp_path / "elsewhere.toml", "limit = 12\n")
        settings = load_settings(
            {CONFIG_OPTION: str(named)}, start=tmp_path, environ={}
        )
        assert settings.limit == 12

    def test_the_named_file_stands_in_for_the_search(self, tmp_path) -> None:
        """A project file at *start* is not read once a file is named."""
        _write(tmp_path / CONFIG_FILENAME, "limit = 3\nmodel = 'near'\n")
        named = _write(tmp_path / "elsewhere.toml", "limit = 12\n")

        settings = load_settings(
            {CONFIG_OPTION: str(named)}, start=tmp_path, environ={}
        )
        assert settings.limit == 12
        assert settings.model == ""

    def test_the_user_file_still_applies(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        _write(tmp_path / "cfg" / "ish" / CONFIG_BASENAME, "model = 'user'\n")
        named = _write(tmp_path / "named.toml", "limit = 12\n")

        settings = load_settings(
            {CONFIG_OPTION: str(named)}, start=tmp_path, environ={}
        )
        assert settings.limit == 12
        assert settings.model == "user"

    def test_the_environment_names_a_file(self, tmp_path) -> None:
        named = _write(tmp_path / "named.toml", "limit = 13\n")
        settings = load_settings(start=tmp_path, environ={CONFIG_ENV_VAR: str(named)})
        assert settings.limit == 13

    def test_the_flag_wins_over_the_environment(self, tmp_path) -> None:
        flag = _write(tmp_path / "flag.toml", "limit = 1\n")
        variable = _write(tmp_path / "variable.toml", "limit = 2\n")

        settings = load_settings(
            {CONFIG_OPTION: str(flag)},
            start=tmp_path,
            environ={CONFIG_ENV_VAR: str(variable)},
        )
        assert settings.limit == 1

    def test_the_variable_is_not_reported_as_an_unknown_option(
        self, tmp_path, caplog
    ) -> None:
        named = _write(tmp_path / "named.toml", "limit = 4\n")
        with caplog.at_level("WARNING"):
            load_settings(start=tmp_path, environ={CONFIG_ENV_VAR: str(named)})
        assert caplog.text == ""

    def test_a_missing_file_is_an_error(self, tmp_path) -> None:
        """The caller named this one, so silence would apply defaults."""
        with pytest.raises(ConfigError, match="no such config file"):
            load_settings(
                {CONFIG_OPTION: str(tmp_path / "gone.toml")},
                start=tmp_path,
                environ={},
            )

    def test_a_directory_of_that_name_is_an_error(self, tmp_path) -> None:
        (tmp_path / "dir.toml").mkdir()
        with pytest.raises(ConfigError, match="no such config file"):
            load_settings(
                {CONFIG_OPTION: str(tmp_path / "dir.toml")},
                start=tmp_path,
                environ={},
            )

    def test_a_home_prefix_is_expanded(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write(tmp_path / "named.toml", "limit = 15\n")

        settings = load_settings(
            start=tmp_path, environ={CONFIG_ENV_VAR: "~/named.toml"}
        )
        assert settings.limit == 15


class TestToolTable:
    """Verify that a file may keep the options under ``[tool.ish]``.

    One file then holds sections for several tools, the way a
    ``pyproject.toml`` holds ``[tool.black]`` beside the rest.
    """

    def test_options_come_from_the_tool_table(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, "[tool.ish]\nlimit = 21\n")
        assert load_settings(start=tmp_path, environ={}).limit == 21

    def test_a_sibling_tool_is_not_an_unknown_option(self, tmp_path, caplog) -> None:
        """A table under ``tool`` belongs to another tool, so say nothing."""
        _write(
            tmp_path / CONFIG_FILENAME,
            "[tool.black]\nline-length = 88\n\n[tool.ish]\nlimit = 21\n",
        )
        with caplog.at_level("WARNING"):
            settings = load_settings(start=tmp_path, environ={})
        assert settings.limit == 21
        assert caplog.text == ""

    def test_a_foreign_top_level_key_is_passed_over(self, tmp_path, caplog) -> None:
        _write(
            tmp_path / CONFIG_FILENAME,
            "[project]\nname = 'other'\n\n[tool.ish]\nlimit = 21\n",
        )
        with caplog.at_level("WARNING"):
            settings = load_settings(start=tmp_path, environ={})
        assert settings.limit == 21
        assert caplog.text == ""

    def test_a_flat_file_still_reads_every_key(self, tmp_path) -> None:
        """A file written before this keeps working, unchanged."""
        _write(tmp_path / CONFIG_FILENAME, "limit = 21\nmodel = 'flat'\n")
        settings = load_settings(start=tmp_path, environ={})
        assert settings.limit == 21
        assert settings.model == "flat"

    def test_a_tool_table_without_ish_reads_the_file_flat(
        self, tmp_path, caplog
    ) -> None:
        """Nothing claims the file, so it is an ish file with a stray key."""
        _write(tmp_path / CONFIG_FILENAME, "limit = 21\n\n[tool.black]\nskip = 1\n")
        with caplog.at_level("WARNING"):
            settings = load_settings(start=tmp_path, environ={})
        assert settings.limit == 21
        assert "tool" in caplog.text

    def test_a_tool_ish_that_is_not_a_table_is_an_error(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, "[tool]\nish = 'yes'\n")
        with pytest.raises(ConfigError, match="'tool.ish' is not a table"):
            load_settings(start=tmp_path, environ={})

    def test_a_tool_that_is_not_a_table_is_an_error(self, tmp_path) -> None:
        _write(tmp_path / CONFIG_FILENAME, "tool = 'black'\n")
        with pytest.raises(ConfigError, match="'tool' is not a table"):
            load_settings(start=tmp_path, environ={})
