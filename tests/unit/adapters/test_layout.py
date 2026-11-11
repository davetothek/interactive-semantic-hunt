"""Test that each adapter module is named for its role.

A plain module name in a package with a registry is one backend, and an
underscore prefix is machinery. A backend that needs a second file is a
subpackage of the same plain name. These guards read the registries, so
a module named against the rule fails here and not in a review.
"""

import importlib
import pkgutil
from collections.abc import Callable, Iterable

import pytest

from ish.adapters.authenticator import AUTHENTICATORS
from ish.adapters.embedder import EMBEDDERS
from ish.adapters.language_model import LANGUAGE_MODELS
from ish.adapters.parser import PARSERS

REGISTRIES: dict[str, Iterable[Callable]] = {
    "ish.adapters.parser": [language.build for language in PARSERS.values()],
    "ish.adapters.embedder": EMBEDDERS.values(),
    "ish.adapters.language_model": LANGUAGE_MODELS.values(),
    "ish.adapters.authenticator": AUTHENTICATORS.values(),
}


def _home(package: str, backend: Callable) -> str:
    """Return the name of the module or subpackage directly under *package*."""
    return backend.__module__.removeprefix(package + ".").split(".")[0]


def _plain_modules(package: str) -> set[str]:
    """Return the modules and subpackages of *package* with no underscore prefix."""
    names = (
        info.name
        for info in pkgutil.iter_modules(importlib.import_module(package).__path__)
    )
    return {name for name in names if not name.startswith("_")}


@pytest.mark.parametrize("package", sorted(REGISTRIES))
class TestLayout:
    """Guard the names of the modules of each package with a registry."""

    def test_every_backend_lives_in_a_plain_module(self, package: str) -> None:
        for backend in REGISTRIES[package]:
            home = _home(package, backend)
            assert not home.startswith("_"), (backend.__qualname__, home)

    def test_each_backend_has_a_module_of_its_own(self, package: str) -> None:
        homes = [_home(package, backend) for backend in REGISTRIES[package]]
        assert len(homes) == len(set(homes)), sorted(homes)

    def test_every_plain_module_holds_a_backend(self, package: str) -> None:
        homes = {_home(package, backend) for backend in REGISTRIES[package]}
        assert _plain_modules(package) <= homes
