"""Where a server keeps the login tokens it gave.

``LoginFile`` is the one store: a file of JSON lines beside the indexes.
"""

from ish.adapters.login_store.file import LoginFile

__all__ = ["LoginFile"]
