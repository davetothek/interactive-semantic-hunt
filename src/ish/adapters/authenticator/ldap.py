"""Sign users in against an LDAP directory, with python-ldap.

The ``ldap`` extra installs python-ldap, and only a server that names
``ldap`` in ``auth`` imports it. A sign-in binds to the directory as the
user, with the distinguished name that ``ldap_user_dn`` makes from the
user name, and the password. With ``ldap_group``, the user must also be
a member of that group.

The user name is escaped before it reaches the directory, both in the
name of the bind and in the filter of the group search. An empty
password is refused before the bind, because many directories accept an
empty password as an anonymous bind.
"""

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from ish.application.ports.authenticator import (
    Identity,
    SignInRefused,
    SignInUnavailable,
)

log = logging.getLogger(__name__)

# The same text for a wrong name and a wrong password, so a refusal
# tells nobody which user names exist.
REFUSED = "The directory refused the user name or the password."

# How long a directory may take to connect and to answer.
TIMEOUT_SECONDS = 10

# The mark in `ldap_user_dn` where the escaped user name goes.
USER_MARK = "{user}"


class LdapDirectory:
    """Check a user name and a password with a bind to an LDAP directory."""

    def __init__(
        self, url: str, user_dn: str, group: str = "", starttls: bool = False
    ) -> None:
        """Bind to the directory at *url* as the name *user_dn* makes.

        Raise ``ValueError`` for no URL, a URL that is not ldap or ldaps,
        a *user_dn* with no ``{user}``, and when python-ldap is absent.
        Warn once for a plain ldap URL with no StartTLS.
        """
        parts = urlsplit(url)
        if parts.scheme not in ("ldap", "ldaps") or not parts.netloc:
            raise ValueError(
                "Set ldap_url to the ldaps:// or ldap:// URL of the directory."
            )
        if USER_MARK not in user_dn:
            raise ValueError(
                "Set ldap_user_dn to the name a user binds as, with {user} where "
                "the user name goes, such as uid={user},ou=people,dc=example,dc=com."
            )
        try:
            import ldap
        except ModuleNotFoundError as exc:
            raise ValueError(
                "LDAP sign-in needs python-ldap. Install the ldap extra: "
                "pip install 'interactive-semantic-hunt[ldap]'"
            ) from exc
        self._ldap: Any = ldap
        self._url = url
        self._host = parts.netloc
        self._user_dn = user_dn
        self._group = group
        self._starttls = starttls
        if parts.scheme == "ldap" and not starttls:
            log.warning(
                "The directory at %s gets each password in plain text. "
                "Use an ldaps URL, or set ldap_starttls = true.",
                self._host,
            )

    @classmethod
    def configured(cls, options: Mapping[str, str]) -> "LdapDirectory":
        """Build from the ``ldap_*`` values in *options*."""
        return cls(
            options["ldap_url"],
            options["ldap_user_dn"],
            options["ldap_group"],
            options["ldap_starttls"] == "true",
        )

    def sign_in(self, user: str, password: str) -> Identity:
        """Return the identity of *user* when the directory accepts *password*.

        Raise ``SignInRefused`` for a wrong name or password, and for a
        user outside ``ldap_group``. Raise ``SignInUnavailable`` when the
        directory cannot be reached or fails. No text holds the password.
        """
        name = user.strip()
        if not name or not password:
            raise SignInRefused(REFUSED)
        ldap = self._ldap
        dn = self._user_dn.replace(USER_MARK, ldap.dn.escape_dn_chars(name))
        connection = None
        try:
            connection = ldap.initialize(self._url)
            connection.set_option(ldap.OPT_REFERRALS, 0)
            connection.set_option(ldap.OPT_NETWORK_TIMEOUT, TIMEOUT_SECONDS)
            connection.set_option(ldap.OPT_TIMEOUT, TIMEOUT_SECONDS)
            connection.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_DEMAND)
            connection.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
            if self._starttls:
                connection.start_tls_s()
            connection.simple_bind_s(dn, password)
            if self._group and not self._member(connection, dn, name):
                raise SignInRefused(
                    f"The directory accepted the password, but {name} is not in "
                    "the group of this server."
                )
        except ldap.INVALID_CREDENTIALS:
            raise SignInRefused(REFUSED) from None
        except (ldap.SERVER_DOWN, ldap.TIMEOUT, ldap.CONNECT_ERROR):
            raise SignInUnavailable(
                f"The directory at {self._host} cannot be reached."
            ) from None
        except ldap.LDAPError as exc:
            raise SignInUnavailable(
                f"The directory at {self._host} failed: {_description(exc)}"
            ) from None
        finally:
            if connection is not None:
                _unbind(connection)
        return Identity(name, "ldap")

    def _member(self, connection: Any, dn: str, name: str) -> bool:
        """Return True when the group holds the user, by name or by uid."""
        escape = self._ldap.filter.escape_filter_chars
        query = (
            f"(|(member={escape(dn)})(uniqueMember={escape(dn)})"
            f"(memberUid={escape(name)}))"
        )
        found = connection.search_s(self._group, self._ldap.SCOPE_BASE, query, ["1.1"])
        return bool(found)


def _description(exc: Exception) -> str:
    """Return what python-ldap says went wrong. It never holds the password."""
    detail = exc.args[0] if exc.args else None
    if isinstance(detail, dict):
        return str(detail.get("desc") or "unknown error")
    return type(exc).__name__


def _unbind(connection: Any) -> None:
    """Close *connection*. A connection that never opened has nothing to close."""
    try:
        connection.unbind_s()
    except Exception:
        log.debug("The connection to the directory did not close cleanly.")
