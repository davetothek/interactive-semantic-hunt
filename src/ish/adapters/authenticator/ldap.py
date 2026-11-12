"""Sign users in against an LDAP directory, with python-ldap.

The ``ldap`` extra installs python-ldap, and only a server that names
``ldap`` in ``auth`` imports it. A sign-in finds the distinguished name
of the user, then binds to the directory with it and the password.

There are two ways to find the name. With ``ldap_user_dn``, the name is
made from the user name, such as ``uid={user},ou=people,...``. With a
service account, ``ldap_bind_dn`` and its password, the server binds as
that account, searches ``ldap_base`` with ``ldap_user_filter``, and takes
the one entry it finds. With ``ldap_group``, the user must also be a
member of that group, which the service account checks when there is
one.

The user name is escaped before it reaches the directory, in the name
of a bind and in each filter. An empty password is refused before the
bind, because many directories accept an empty password as an anonymous
bind. The password of the service account comes from the environment or
from a file, never from a config file.
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

# What a client reads when the directory cannot check the password.
UNREACHABLE = "The directory cannot be reached. Try again later."
FAILED = "The directory failed. The log of the server says why."

# How long a directory may take to connect and to answer.
TIMEOUT_SECONDS = 10

# The mark in `ldap_user_dn` and `ldap_user_filter` where the escaped
# user name goes.
USER_MARK = "{user}"

# The filter that finds a user, for a directory with `uid` names. An
# Active Directory uses (sAMAccountName={user}).
DEFAULT_FILTER = "(uid={user})"

# Where the password of the service account comes from. A config file
# can be read by others and committed, so it never holds it.
BIND_PASSWORD_VARIABLE = "ISH_LDAP_BIND_PASSWORD"


class LdapDirectory:
    """Check a user name and a password with a bind to an LDAP directory."""

    def __init__(
        self,
        url: str,
        user_dn: str = "",
        group: str = "",
        starttls: bool = False,
        *,
        bind_dn: str = "",
        bind_password: str = "",
        base: str = "",
        user_filter: str = DEFAULT_FILTER,
    ) -> None:
        """Bind to the directory at *url*.

        Find the name of a user from *user_dn*, or else by a search of
        *base* with *user_filter* as the service account *bind_dn*. Raise
        ``ValueError`` for no URL, a URL that is not ldap or ldaps, no way
        or both ways to find a name, a template with no ``{user}``, a
        service account with no password or no base, and when python-ldap
        is absent. Warn once for a plain ldap URL with no StartTLS.
        """
        parts = urlsplit(url)
        if parts.scheme not in ("ldap", "ldaps") or not parts.netloc:
            raise ValueError(
                "Set ldap_url to the ldaps:// or ldap:// URL of the directory."
            )
        if bool(user_dn) == bool(bind_dn):
            raise ValueError(
                "Set ldap_user_dn to make the name a user binds as, or "
                "ldap_bind_dn and ldap_base to search for it as a service "
                "account. Set one of the two."
            )
        if user_dn and USER_MARK not in user_dn:
            raise ValueError(
                "Set ldap_user_dn to the name a user binds as, with {user} where "
                "the user name goes, such as uid={user},ou=people,dc=example,dc=com."
            )
        if bind_dn:
            if not bind_password:
                raise ValueError(
                    f"Set {BIND_PASSWORD_VARIABLE}, or {BIND_PASSWORD_VARIABLE}_FILE, "
                    "to the password of the service account in ldap_bind_dn."
                )
            if not base:
                raise ValueError(
                    "Set ldap_base to the part of the directory to search."
                )
            if USER_MARK not in user_filter:
                raise ValueError(
                    "Set ldap_user_filter to a filter with {user} where the user "
                    "name goes, such as (uid={user}) or (sAMAccountName={user})."
                )
        try:
            import ldap

            # `import ldap` loads `ldap.dn` and not `ldap.filter`, so import
            # each function from its own module.
            from ldap.dn import escape_dn_chars
            from ldap.filter import escape_filter_chars
        except ModuleNotFoundError as exc:
            raise ValueError(
                "LDAP sign-in needs python-ldap. Install the ldap extra: "
                "pip install 'interactive-semantic-hunt[ldap]'"
            ) from exc
        if not all(
            hasattr(ldap, name)
            for name in ("OPT_X_TLS_REQUIRE_CERT", "OPT_X_TLS_NEWCTX")
        ):
            raise ValueError(
                "This python-ldap has no TLS, so it cannot check the certificate "
                "of the directory. Install a python-ldap built with TLS."
            )
        self._ldap: Any = ldap
        self._escape_dn: Any = escape_dn_chars
        self._escape_filter: Any = escape_filter_chars
        self._url = url
        self._host = parts.netloc
        self._user_dn = user_dn
        self._group = group
        self._starttls = starttls
        self._bind_dn = bind_dn
        self._bind_password = bind_password
        self._base = base
        self._user_filter = user_filter
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
            bind_dn=options["ldap_bind_dn"],
            bind_password=options["ldap_bind_password"],
            base=options["ldap_base"],
            user_filter=options["ldap_user_filter"] or DEFAULT_FILTER,
        )

    def sign_in(self, user: str, password: str) -> Identity:
        """Return the identity of *user* when the directory accepts *password*.

        Raise ``SignInRefused`` for a wrong name or password, and for a
        user outside ``ldap_group``. Raise ``SignInUnavailable`` when the
        directory cannot be reached or fails, and when it refuses the
        service account. No text holds a password.
        """
        name = user.strip()
        if not name or not password:
            raise SignInRefused(REFUSED)
        ldap = self._ldap
        connection = None
        try:
            connection = self._open()
            if self._bind_dn:
                dn = self._find(connection, name)
            else:
                dn = self._user_dn.replace(USER_MARK, self._escape_dn(name))
            if self._bind_dn and self._group and not self._member(connection, dn, name):
                # Ask the group as the service account, before the password,
                # so no answer depends on whether the password is right.
                self._not_in_group(name)
            connection.simple_bind_s(dn, password)
            if (
                not self._bind_dn
                and self._group
                and not self._member(connection, dn, name)
            ):
                self._not_in_group(name)
        except ldap.INVALID_CREDENTIALS:
            raise SignInRefused(REFUSED) from None
        except (ldap.SERVER_DOWN, ldap.TIMEOUT, ldap.CONNECT_ERROR):
            # A client may not be signed in yet, so its text names no
            # host of the network. The log of the server does.
            log.error("The directory at %s cannot be reached.", self._host)
            raise SignInUnavailable(UNREACHABLE) from None
        except ldap.LDAPError as exc:
            log.error("The directory at %s failed: %s", self._host, _description(exc))
            raise SignInUnavailable(FAILED) from None
        finally:
            if connection is not None:
                _unbind(connection)
        return Identity(name, "ldap")

    def _open(self) -> Any:
        """Return a connection that checks the certificate.

        Bind it as the service account when there is one.
        """
        ldap = self._ldap
        connection = ldap.initialize(self._url)
        connection.set_option(ldap.OPT_REFERRALS, 0)
        connection.set_option(ldap.OPT_NETWORK_TIMEOUT, TIMEOUT_SECONDS)
        connection.set_option(ldap.OPT_TIMEOUT, TIMEOUT_SECONDS)
        connection.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_DEMAND)
        connection.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
        if self._starttls:
            connection.start_tls_s()
        if self._bind_dn:
            try:
                connection.simple_bind_s(self._bind_dn, self._bind_password)
            except ldap.INVALID_CREDENTIALS:
                # The fault of the server, not of the user, so it is no
                # wrong password and counts as no failure of the user.
                log.error(
                    "The directory at %s refused the service account %s.",
                    self._host,
                    self._bind_dn,
                )
                raise SignInUnavailable(FAILED) from None
        return connection

    def _find(self, connection: Any, name: str) -> str:
        """Return the name of the one entry of *name*, or raise ``SignInRefused``."""
        query = self._user_filter.replace(USER_MARK, self._escape_filter(name))
        found = connection.search_s(
            self._base, self._ldap.SCOPE_SUBTREE, query, ["1.1"]
        )
        # A search can return references to other servers, with no name.
        names = [dn for dn, _attributes in found if dn]
        if len(names) != 1:
            log.info(
                "The directory holds %d entries for %s.", len(names), printable(name)
            )
            raise SignInRefused(REFUSED)
        return names[0]

    def _not_in_group(self, name: str) -> None:
        # The same text as a wrong password, so a refusal does not tell a
        # guesser that the password was right.
        log.info("%s is not in the group of this server.", printable(name))
        raise SignInRefused(REFUSED)

    def _member(self, connection: Any, dn: str, name: str) -> bool:
        """Return True when the group holds the user, by name or by uid."""
        escape = self._escape_filter
        query = (
            f"(|(member={escape(dn)})(uniqueMember={escape(dn)})"
            f"(memberUid={escape(name)}))"
        )
        found = connection.search_s(self._group, self._ldap.SCOPE_BASE, query, ["1.1"])
        return bool(found)


def printable(text: str) -> str:
    """Return *text* with each character that is not printable escaped."""
    return "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in text)


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
