"""Find the Ollama daemon that the embedder and the language model share.

Both adapters read the host the same way: an explicit option, then
``OLLAMA_HOST``, then the default port on this machine. A bare
``host:port`` is accepted, because that is how Ollama documents the
variable. Both also show a refusal of the daemon the same way.
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


def error_text(detail: str) -> str:
    """Return the message of an error body of the daemon, else *detail*.

    The daemon puts the reason for a refusal in the ``error`` field of a
    JSON body. A reader needs that sentence, not the JSON around it.
    """
    # Only the error paths call this, so pay the import here.
    import json

    try:
        body = json.loads(detail)
    except ValueError:
        return detail
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return detail
