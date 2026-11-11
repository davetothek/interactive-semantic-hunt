"""Sign this machine in to an ish server over HTTP.

``HttpLogin`` is the one way: the sign-in path beside ``/mcp``.
"""

from ish.adapters.login.http import HttpLogin

__all__ = ["HttpLogin"]
