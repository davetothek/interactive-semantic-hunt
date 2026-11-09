"""Find the Ollama daemon that the embedder and the language model share.

Both adapters read the host the same way: an explicit option, then
``OLLAMA_HOST``, then the default port on this machine. A bare
``host:port`` is accepted, because that is how Ollama documents the
variable.
"""

import os

DEFAULT_HOST = "http://localhost:11434"


def resolve_host(host: str | None) -> str:
    """Return the daemon URL: *host*, else ``OLLAMA_HOST``, else this machine."""
    return normalize_host(host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST)


def normalize_host(host: str) -> str:
    """Accept a bare ``host:port`` as well as a full URL."""
    host = host.rstrip("/")
    if not host.startswith(("http://", "https://")):
        return f"http://{host}"
    return host


def is_loopback(host: str) -> bool:
    """Return True when the host names this machine."""
    # Only the error paths call this, so pay the imports here.
    import ipaddress
    import urllib.parse

    name = urllib.parse.urlsplit(host).hostname or ""
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False
