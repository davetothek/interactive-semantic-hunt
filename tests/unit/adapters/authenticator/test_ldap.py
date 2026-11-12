"""Test the sign-in against an LDAP directory, with a stand-in for python-ldap."""

import logging
import sys

import pytest

from ish.adapters.authenticator import AUTHENTICATORS
from ish.adapters.authenticator.ldap import REFUSED, LdapDirectory
from ish.application.ports.authenticator import (
    Identity,
    SignIn,
    SignInRefused,
    SignInUnavailable,
)
from tests.unit.adapters.authenticator import fake_ldap
from tests.unit.adapters.authenticator.fake_ldap import Directory

URL = "ldaps://ldap.example.invalid"
TEMPLATE = "uid={user},ou=people,dc=example,dc=invalid"
READER = "uid=reader,ou=people,dc=example,dc=invalid"
GROUP = "cn=ish,ou=groups,dc=example,dc=invalid"
PASSWORD = "hunter2-EXAMPLE"


@pytest.fixture()
def directory(monkeypatch) -> Directory:
    made = Directory(users={READER: PASSWORD})
    fake_ldap.install(monkeypatch, made)
    return made


def _method(**changes) -> LdapDirectory:
    options = {"url": URL, "user_dn": TEMPLATE, "group": "", "starttls": False}
    return LdapDirectory(**(options | changes))


class TestSignIn:
    def test_it_is_a_sign_in_method(self, directory: Directory) -> None:
        assert isinstance(_method(), SignIn)

    def test_the_password_of_the_user_signs_in(self, directory: Directory) -> None:
        assert _method().sign_in("reader", PASSWORD) == Identity("reader", "ldap")
        assert directory.binds == [(READER, PASSWORD)]
        assert directory.urls == [URL]
        assert directory.unbound == 1

    @pytest.mark.parametrize(("user", "password"), [("reader", ""), (" ", PASSWORD)])
    def test_an_empty_name_or_password_is_no_anonymous_bind(
        self, user: str, password: str, directory: Directory
    ) -> None:
        # Many directories accept an empty password as an anonymous bind.
        with pytest.raises(SignInRefused, match=REFUSED):
            _method().sign_in(user, password)
        assert directory.binds == []

    def test_the_name_is_stripped(self, directory: Directory) -> None:
        assert _method().sign_in(" reader ", PASSWORD).user == "reader"

    @pytest.mark.parametrize(
        ("user", "password"), [("reader", "wrong-EXAMPLE"), ("nobody", PASSWORD)]
    )
    def test_a_wrong_name_or_password_gets_one_text(
        self, user: str, password: str, directory: Directory
    ) -> None:
        with pytest.raises(SignInRefused) as refused:
            _method().sign_in(user, password)
        assert str(refused.value) == REFUSED
        assert password not in str(refused.value)
        assert directory.unbound == 1

    def test_the_connection_checks_the_certificate_and_has_a_time_limit(
        self, directory: Directory
    ) -> None:
        _method().sign_in("reader", PASSWORD)
        ldap = sys.modules["ldap"]
        assert directory.options[ldap.OPT_X_TLS_REQUIRE_CERT] == ldap.OPT_X_TLS_DEMAND
        assert directory.options[ldap.OPT_NETWORK_TIMEOUT] == 10
        assert directory.options[ldap.OPT_TIMEOUT] == 10
        assert directory.options[ldap.OPT_REFERRALS] == 0

    def test_the_certificate_rule_is_set_before_the_new_tls_context(
        self, directory: Directory
    ) -> None:
        # python-ldap applies a TLS option to the context it makes next.
        _method().sign_in("reader", PASSWORD)
        ldap = sys.modules["ldap"]
        order = list(directory.options)
        assert order.index(ldap.OPT_X_TLS_REQUIRE_CERT) < order.index(
            ldap.OPT_X_TLS_NEWCTX
        )

    def test_a_python_ldap_with_no_tls_is_refused(
        self, directory: Directory, monkeypatch
    ) -> None:
        monkeypatch.delattr(sys.modules["ldap"], "OPT_X_TLS_REQUIRE_CERT")
        with pytest.raises(ValueError, match="This python-ldap has no TLS"):
            _method()

    def test_starttls_runs_before_the_bind(self, directory: Directory) -> None:
        _method(url="ldap://ldap.example.invalid", starttls=True).sign_in(
            "reader", PASSWORD
        )
        assert directory.tls_started == 1


class TestGroup:
    def test_a_member_of_the_group_signs_in(self, directory: Directory) -> None:
        directory.groups[GROUP] = {READER}
        assert _method(group=GROUP).sign_in("reader", PASSWORD).user == "reader"
        ((base, _scope, query),) = directory.searches
        assert base == GROUP
        assert query == (
            f"(|(member={READER})(uniqueMember={READER})(memberUid=reader))"
        )

    def test_a_member_by_uid_signs_in(self, directory: Directory) -> None:
        directory.groups[GROUP] = {"reader"}
        assert _method(group=GROUP).sign_in("reader", PASSWORD).user == "reader"

    def test_a_user_outside_the_group_gets_the_text_of_a_wrong_password(
        self, directory: Directory, caplog
    ) -> None:
        # A different text would tell a guesser that the password was right.
        directory.groups[GROUP] = {"uid=other,ou=people,dc=example,dc=invalid"}
        with caplog.at_level(logging.INFO, logger="ish"):
            with pytest.raises(SignInRefused) as refused:
                _method(group=GROUP).sign_in("reader", PASSWORD)
        assert str(refused.value) == REFUSED
        assert "reader is not in the group of this server." in caplog.text

    def test_the_group_search_escapes_the_user_name(self, directory: Directory) -> None:
        directory.users["uid=r*(x),ou=people,dc=example,dc=invalid"] = PASSWORD
        directory.groups[GROUP] = set()
        with pytest.raises(SignInRefused):
            _method(group=GROUP).sign_in("r*(x)", PASSWORD)
        ((_base, _scope, query),) = directory.searches
        assert r"(memberUid=r\2a\28x\29)" in query
        assert "(memberUid=r*(x))" not in query


class TestFailure:
    def test_a_directory_that_is_down_is_not_a_wrong_password(
        self, directory: Directory, caplog
    ) -> None:
        directory.down = True
        with caplog.at_level(logging.ERROR):
            with pytest.raises(SignInUnavailable) as failed:
                _method().sign_in("reader", PASSWORD)
        # A client may not be signed in yet, so its text names no host.
        assert str(failed.value) == "The directory cannot be reached. Try again later."
        assert caplog.messages == [
            "The directory at ldap.example.invalid cannot be reached."
        ]

    @pytest.mark.parametrize(
        ("failure", "text"),
        [
            (fake_ldap.LDAPError({"desc": "Busy", "info": "x"}), "Busy"),
            (fake_ldap.LDAPError("not a dict"), "LDAPError"),
            (fake_ldap.LDAPError(), "LDAPError"),
            (fake_ldap.LDAPError({"info": "x"}), "unknown error"),
        ],
    )
    def test_another_failure_names_what_the_directory_said_in_the_log(
        self, failure: Exception, text: str, directory: Directory, caplog
    ) -> None:
        directory.failure = failure
        with caplog.at_level(logging.ERROR):
            with pytest.raises(SignInUnavailable) as failed:
                _method().sign_in("reader", PASSWORD)
        assert str(failed.value) == (
            "The directory failed. The log of the server says why."
        )
        assert caplog.messages == [
            f"The directory at ldap.example.invalid failed: {text}"
        ]
        assert failed.value.__cause__ is None

    def test_a_connection_that_does_not_close_cleanly(
        self, directory: Directory, caplog
    ) -> None:
        directory.unbind_fails = True
        with caplog.at_level(logging.DEBUG, logger="ish"):
            assert _method().sign_in("reader", PASSWORD).user == "reader"
        assert "did not close cleanly" in caplog.text

    def test_a_connection_that_never_opened_closes_nothing(
        self, directory: Directory, monkeypatch
    ) -> None:
        def unreachable(url):
            raise fake_ldap.SERVER_DOWN({"desc": "Can't contact LDAP server"})

        monkeypatch.setattr(sys.modules["ldap"], "initialize", unreachable)
        with pytest.raises(SignInUnavailable):
            _method().sign_in("reader", PASSWORD)
        assert directory.unbound == 0


class TestConfiguration:
    def test_the_registry_names_it_ldap(self) -> None:
        assert AUTHENTICATORS["ldap"] == LdapDirectory.configured

    def test_it_reads_its_options(self, directory: Directory) -> None:
        method = LdapDirectory.configured(
            {
                "ldap_url": "ldap://ldap.example.invalid",
                "ldap_user_dn": TEMPLATE,
                "ldap_bind_dn": "",
                "ldap_bind_password": "",
                "ldap_base": "",
                "ldap_user_filter": "",
                "ldap_group": GROUP,
                "ldap_starttls": "true",
                "token": "not for this method",
            }
        )
        directory.groups[GROUP] = {READER}
        method.sign_in("reader", PASSWORD)
        assert directory.tls_started == 1
        assert directory.searches[0][0] == GROUP

    @pytest.mark.parametrize("url", ["", "https://ldap.example.invalid", "ldaps://"])
    def test_a_url_that_is_no_directory_is_refused(
        self, url: str, directory: Directory
    ) -> None:
        with pytest.raises(ValueError, match="Set ldap_url"):
            _method(url=url)

    def test_a_name_with_no_place_for_the_user_is_refused(
        self, directory: Directory
    ) -> None:
        with pytest.raises(ValueError, match=r"with \{user\} where the user name goes"):
            _method(user_dn="ou=people,dc=example,dc=invalid")

    def test_no_python_ldap_names_the_extra(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "ldap", None)
        with pytest.raises(ValueError) as refused:
            _method()
        assert str(refused.value) == (
            "LDAP sign-in needs python-ldap. Install the ldap extra: "
            "pip install 'interactive-semantic-hunt[ldap]'"
        )


class TestTls:
    @pytest.mark.parametrize("name", ["OPT_X_TLS_REQUIRE_CERT", "OPT_X_TLS_NEWCTX"])
    def test_a_python_ldap_without_either_tls_option_is_refused(
        self, name: str, directory: Directory, monkeypatch
    ) -> None:
        monkeypatch.delattr(sys.modules["ldap"], name)
        with pytest.raises(ValueError, match="This python-ldap has no TLS"):
            _method()


SERVICE = "cn=ish,ou=services,dc=example,dc=invalid"
SERVICE_PASSWORD = "service-EXAMPLE"
BASE = "ou=people,dc=example,dc=invalid"


def _service(**changes) -> LdapDirectory:
    options = {
        "url": URL,
        "bind_dn": SERVICE,
        "bind_password": SERVICE_PASSWORD,
        "base": BASE,
    }
    return LdapDirectory(**(options | changes))


class TestServiceAccount:
    """The service account finds the name of a user, then the user binds."""

    @pytest.fixture()
    def found(self, directory: Directory) -> Directory:
        directory.users[SERVICE] = SERVICE_PASSWORD
        directory.lookups["(uid=reader)"] = [READER]
        return directory

    def test_the_account_searches_then_the_user_binds(self, found: Directory) -> None:
        assert _service().sign_in("reader", PASSWORD) == Identity("reader", "ldap")
        assert found.binds == [(SERVICE, SERVICE_PASSWORD), (READER, PASSWORD)]
        ((base, scope, query),) = found.searches
        assert (base, scope, query) == (BASE, fake_ldap.SCOPE_SUBTREE, "(uid=reader)")

    def test_a_filter_of_the_operator_finds_the_user(self, found: Directory) -> None:
        found.lookups["(sAMAccountName=reader)"] = [READER]
        method = _service(user_filter="(sAMAccountName={user})")
        assert method.sign_in("reader", PASSWORD).user == "reader"

    def test_the_user_name_is_escaped_in_the_filter(self, found: Directory) -> None:
        with pytest.raises(SignInRefused):
            _service().sign_in("r*)(uid=*", PASSWORD)
        ((_base, _scope, query),) = found.searches
        assert query == r"(uid=r\2a\29\28uid=\2a)"

    @pytest.mark.parametrize("entries", [[], [READER, "uid=reader,ou=old,dc=x"]])
    def test_no_entry_or_two_entries_bind_nobody(
        self, entries: list, found: Directory
    ) -> None:
        found.lookups["(uid=reader)"] = entries
        with pytest.raises(SignInRefused, match=REFUSED):
            _service().sign_in("reader", PASSWORD)
        assert found.binds == [(SERVICE, SERVICE_PASSWORD)]

    def test_a_reference_to_another_server_is_no_entry(self, found: Directory) -> None:
        found.lookups["(uid=reader)"] = [None, READER]
        assert _service().sign_in("reader", PASSWORD).user == "reader"

    def test_a_wrong_password_of_the_user(self, found: Directory) -> None:
        with pytest.raises(SignInRefused, match=REFUSED):
            _service().sign_in("reader", "wrong-EXAMPLE")

    def test_an_account_the_directory_refuses_is_no_wrong_password(
        self, found: Directory, caplog
    ) -> None:
        found.users[SERVICE] = "rotated-EXAMPLE"
        with caplog.at_level(logging.ERROR):
            with pytest.raises(SignInUnavailable) as failed:
                _service().sign_in("reader", PASSWORD)
        assert (
            str(failed.value) == "The directory failed. The log of the server says why."
        )
        assert caplog.messages == [
            "The directory at ldap.example.invalid refused the service account "
            f"{SERVICE}."
        ]
        assert SERVICE_PASSWORD not in caplog.text
        assert found.binds == [(SERVICE, SERVICE_PASSWORD)]

    def test_the_group_is_asked_before_the_password(self, found: Directory) -> None:
        found.groups[GROUP] = set()
        with pytest.raises(SignInRefused, match=REFUSED):
            _service(group=GROUP).sign_in("reader", PASSWORD)
        # The password of the user never reached the directory.
        assert found.binds == [(SERVICE, SERVICE_PASSWORD)]

    def test_a_member_of_the_group_signs_in(self, found: Directory) -> None:
        found.groups[GROUP] = {READER}
        assert _service(group=GROUP).sign_in("reader", PASSWORD).user == "reader"
        assert found.binds == [(SERVICE, SERVICE_PASSWORD), (READER, PASSWORD)]

    def test_it_reads_its_options(self, found: Directory) -> None:
        method = LdapDirectory.configured(
            {
                "ldap_url": URL,
                "ldap_user_dn": "",
                "ldap_bind_dn": SERVICE,
                "ldap_bind_password": SERVICE_PASSWORD,
                "ldap_base": BASE,
                "ldap_user_filter": "",
                "ldap_group": "",
                "ldap_starttls": "",
            }
        )
        assert method.sign_in("reader", PASSWORD).user == "reader"
        assert found.searches[0][2] == "(uid=reader)"

    @pytest.mark.parametrize(
        ("changes", "error"),
        [
            ({"user_dn": TEMPLATE}, "Set one of the two."),
            ({"bind_dn": ""}, "Set one of the two."),
            (
                {"bind_password": ""},
                "ISH_LDAP_BIND_PASSWORD, or ISH_LDAP_BIND_PASSWORD_FILE",
            ),
            ({"base": ""}, "Set ldap_base"),
            ({"user_filter": "(uid=reader)"}, "Set ldap_user_filter"),
        ],
    )
    def test_a_setting_that_cannot_work_is_refused(
        self, changes: dict, error: str, directory: Directory
    ) -> None:
        with pytest.raises(ValueError, match=error):
            _service(**changes)
