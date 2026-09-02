"""Test loading parsers the user wrote."""

from pathlib import Path

from ish.adapters.parser.plugins import load_parsers, plugin_dir
from ish.application.ports.parser import Parser

GOOD = '''
from pathlib import Path
from ish.domain.chunk import Chunk


class Toy:
    language = "toy"
    suffixes = frozenset({".toy"})

    def parse(self, path, source):
        return [Chunk(path=path, text=source, kind="document",
                      language="toy", symbol=path.stem,
                      start_line=1, end_line=len(source.splitlines()) or 1)]


def parser():
    return Toy()
'''


def write(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body)
    return path


class TestDiscovery:
    """Verify a parser is found and usable."""

    def test_a_parser_is_loaded(self, tmp_path: Path) -> None:
        write(tmp_path, "toy.py", GOOD)
        found = load_parsers(tmp_path)
        assert set(found) == {"toy"}

    def test_the_loaded_parser_satisfies_the_port(self, tmp_path: Path) -> None:
        write(tmp_path, "toy.py", GOOD)
        assert isinstance(load_parsers(tmp_path)["toy"](), Parser)

    def test_it_parses(self, tmp_path: Path) -> None:
        write(tmp_path, "toy.py", GOOD)
        parser = load_parsers(tmp_path)["toy"]()
        chunks = parser.parse(Path("a.toy"), "one\ntwo\n")
        assert chunks[0].language == "toy"
        assert chunks[0].end_line == 2

    def test_no_directory(self, tmp_path: Path) -> None:
        assert load_parsers(tmp_path / "absent") == {}

    def test_an_empty_directory(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        assert load_parsers(tmp_path) == {}

    def test_a_private_module_is_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "_helper.py", GOOD)
        assert load_parsers(tmp_path) == {}

    def test_only_python_files_are_read(self, tmp_path: Path) -> None:
        write(tmp_path, "notes.txt", "not code")
        assert load_parsers(tmp_path) == {}

    def test_the_default_directory_is_the_user_config(self) -> None:
        """A parser is code, so it comes only from where the user put it."""
        assert plugin_dir().parts[-2:] == ("ish", "parsers")


class TestBadPlugins:
    """Verify one broken parser cannot stop the tool."""

    def _only_broken(self, tmp_path: Path, body: str, caplog) -> dict:
        write(tmp_path, "broken.py", body)
        write(tmp_path, "toy.py", GOOD)
        with caplog.at_level("WARNING"):
            found = load_parsers(tmp_path)
        return found

    def test_a_module_that_raises_on_import(self, tmp_path, caplog) -> None:
        found = self._only_broken(tmp_path, "raise RuntimeError('boom')\n", caplog)
        assert set(found) == {"toy"}
        assert "failed to load" in caplog.text

    def test_a_module_with_no_factory(self, tmp_path, caplog) -> None:
        found = self._only_broken(tmp_path, "x = 1\n", caplog)
        assert set(found) == {"toy"}
        assert "no parser()" in caplog.text

    def test_a_factory_that_raises(self, tmp_path, caplog) -> None:
        body = "def parser():\n    raise ValueError('nope')\n"
        found = self._only_broken(tmp_path, body, caplog)
        assert set(found) == {"toy"}
        assert "could not be built" in caplog.text

    def test_something_that_is_not_a_parser(self, tmp_path, caplog) -> None:
        body = "def parser():\n    return 42\n"
        found = self._only_broken(tmp_path, body, caplog)
        assert set(found) == {"toy"}
        assert "not a parser" in caplog.text

    def test_a_parser_with_no_language(self, tmp_path, caplog) -> None:
        body = (
            "class P:\n"
            "    language = ''\n"
            "    suffixes = frozenset({'.x'})\n"
            "    def parse(self, path, source): return []\n"
            "def parser(): return P()\n"
        )
        found = self._only_broken(tmp_path, body, caplog)
        assert set(found) == {"toy"}
        assert "no language" in caplog.text

    def test_a_parser_claiming_no_suffix(self, tmp_path, caplog) -> None:
        body = (
            "class P:\n"
            "    language = 'p'\n"
            "    suffixes = frozenset()\n"
            "    def parse(self, path, source): return []\n"
            "def parser(): return P()\n"
        )
        found = self._only_broken(tmp_path, body, caplog)
        assert set(found) == {"toy"}
        assert "no suffix" in caplog.text

    def test_a_suffix_without_a_dot(self, tmp_path, caplog) -> None:
        """Discovery matches on Path.suffix, which always has a dot."""
        body = (
            "class P:\n"
            "    language = 'p'\n"
            "    suffixes = frozenset({'toy'})\n"
            "    def parse(self, path, source): return []\n"
            "def parser(): return P()\n"
        )
        found = self._only_broken(tmp_path, body, caplog)
        assert set(found) == {"toy"}
        assert "leading dot" in caplog.text

    def test_two_parsers_claiming_one_language(self, tmp_path, caplog) -> None:
        write(tmp_path, "a_toy.py", GOOD)
        write(tmp_path, "b_toy.py", GOOD)
        with caplog.at_level("WARNING"):
            found = load_parsers(tmp_path)
        assert set(found) == {"toy"}
        assert "claim the language" in caplog.text


class TestRegistryIntegration:
    """Verify how a user parser meets the built-in ones."""

    def test_it_joins_the_registry(self, tmp_path, monkeypatch) -> None:
        from ish import bootstrap
        from ish.settings import Settings

        write(tmp_path, "toy.py", GOOD)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path.parent))
        (tmp_path.parent / "ish").mkdir(exist_ok=True)
        target = tmp_path.parent / "ish" / "parsers"
        target.mkdir(parents=True, exist_ok=True)
        (target / "toy.py").write_text(GOOD)

        available = bootstrap.all_parsers(Settings())
        assert "toy" in available
        assert "python" in available

    def test_a_user_parser_replaces_a_built_in(self, tmp_path, monkeypatch) -> None:
        """This is how a project teaches ish its own dialect."""
        from ish import bootstrap
        from ish.settings import Settings

        body = GOOD.replace('language = "toy"', 'language = "python"').replace(
            'language="toy"', 'language="python"'
        )
        target = tmp_path / "ish" / "parsers"
        target.mkdir(parents=True, exist_ok=True)
        (target / "mine.py").write_text(body)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        built = bootstrap.all_parsers(Settings())["python"]()
        assert built.suffixes == frozenset({".toy"})

    def test_plugins_can_be_turned_off(self, tmp_path, monkeypatch) -> None:
        from dataclasses import replace

        from ish import bootstrap
        from ish.settings import Settings

        target = tmp_path / "ish" / "parsers"
        target.mkdir(parents=True, exist_ok=True)
        (target / "toy.py").write_text(GOOD)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        assert "toy" not in bootstrap.all_parsers(replace(Settings(), plugins=False))


class TestLateFailures:
    """Verify the guards around building a parser after discovery."""

    def test_a_parser_removed_after_discovery(self, tmp_path: Path) -> None:
        """Discovery and use are separate moments."""
        path = write(tmp_path, "toy.py", GOOD)
        factory = load_parsers(tmp_path)["toy"]
        path.unlink()

        with pytest.raises(ValueError, match="no longer be loaded"):
            factory()

    def test_a_file_importlib_cannot_describe(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        import importlib.util

        write(tmp_path, "toy.py", GOOD)
        monkeypatch.setattr(
            importlib.util, "spec_from_file_location", lambda *a, **k: None
        )
        with caplog.at_level("WARNING"):
            assert load_parsers(tmp_path) == {}
        assert "Cannot load a parser" in caplog.text
