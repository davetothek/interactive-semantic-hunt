"""Send one JSON request over HTTP with the standard library.

An adapter that reaches a service over HTTP sends a JSON body and reads
a JSON reply, and the ways that can fail are the same for every service.
``post_json`` raises one exception for each, and the adapter writes the
message its reader needs: which service, and what to do.

The HTTP client costs 20 ms to import, which a search never pays, so the
imports run inside the call.
"""

from typing import Any


class HttpFailure(Exception):
    """A request that brought back no usable reply."""


class Timeout(HttpFailure):
    """The service did not answer in time."""


class Refused(HttpFailure):
    """The service answered with an error status."""

    def __init__(self, code: int, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class Unreachable(HttpFailure):
    """No connection reached the service."""

    def __init__(self, reason: object) -> None:
        super().__init__(str(reason))
        self.reason = reason


class NotJson(HttpFailure):
    """The service answered with a body that is not JSON."""


def post_json(
    url: str,
    payload: object,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict:
    """Post *payload* as JSON to *url* and return the decoded reply.

    Raise ``Timeout``, ``Refused``, ``Unreachable``, or ``NotJson``. Each
    carries the error it stands for as its cause.
    """
    import json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except TimeoutError as exc:
        raise Timeout(str(exc)) from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise Refused(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        raise Unreachable(exc.reason) from exc
    except json.JSONDecodeError as exc:
        raise NotJson(str(exc)) from exc


# The status codes of a redirect.
REDIRECTS = (301, 302, 303, 307, 308)


def open_without_redirects(request: Any, timeout: float) -> Any:
    """Open *request* and return the response. Follow no redirect.

    A redirect sends the request again, with its headers, to an address
    that the server chose. A request that carries a token or a password
    must reach only the address the user gave, so a redirect comes back
    as an ``HTTPError`` with its status. The default handler of https
    checks the certificate of the server.
    """
    import urllib.request

    class _Stay(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    return urllib.request.build_opener(_Stay).open(request, timeout=timeout)
