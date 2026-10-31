"""Fixture discovery validates bounded metadata independently of private reads."""

from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_admin import HTTPMockFixtureService
from nexusflow.modules.connectors.mock_admin_types import HTTPMockFixtureAdminError
from nexusflow.modules.connectors.mock_listing_types import HTTPMockFixtureReceiptPage
from nexusflow.modules.connectors.mock_storage_types import (
    HTTPMockFixtureReceipt,
    HTTPMockStorageError,
)
from nexusflow.modules.identity_environments.types import IdentityError

PRIVATE = "never-publish-fixture-body"
DEV = Scope(organization_id="acme", environment_id="dev")


class Authorization:
    def __init__(self):
        self.calls = []
        self.failure = None

    def authorize(self, scope, permission, resource):
        self.calls.append((scope, permission, resource))
        if self.failure:
            raise self.failure
        return "admin"


def receipt(connection="hr", revision="v1", **changes):
    return replace(
        HTTPMockFixtureReceipt(DEV, connection, revision, "sha256:" + "a" * 64, 100), **changes
    )


class Store:
    def __init__(self, page=None):
        self.page = page or HTTPMockFixtureReceiptPage((receipt(),))
        self.calls = []
        self.failure = None
        self.on_list = lambda scope: None

    def list_metadata(self, scope, **kwargs):
        self.calls.append((scope, kwargs))
        self.on_list(scope)
        if self.failure:
            raise self.failure
        return self.page


def service(store=None, authorization=None, scopes=(DEV,)):
    return HTTPMockFixtureService(store or Store(), authorization or Authorization(), scopes)


def failure(operation, code="unavailable"):
    with pytest.raises(HTTPMockFixtureAdminError) as error:
        operation()
    assert error.value.code == code
    assert PRIVATE not in str(error.value) + repr(error.value)
    assert error.value.__suppress_context__


def test_page_is_metadata_only_with_fresh_authorization_and_independent_scope():
    auth = Authorization()
    original = receipt()
    store = Store(HTTPMockFixtureReceiptPage((original,), "hr", "v1"))
    result = service(store, auth).list(DEV, limit=1)
    assert result.items[0].model_dump() == {
        "scope": DEV.model_dump(),
        "connection_id": "hr",
        "connection_revision": "v1",
        "fixture_digest": "sha256:" + "a" * 64,
        "stored_bytes": 100,
    }
    assert (result.next_after_connection_id, result.next_after_connection_revision) == ("hr", "v1")
    assert auth.calls == [(DEV, "connection.manage", "connections")] * 2
    assert store.calls[0][0] is not DEV
    object.__setattr__(original.scope, "organization_id", "changed")
    try:
        assert result.items[0].scope.organization_id == "acme"
    finally:
        object.__setattr__(original.scope, "organization_id", "acme")


def test_cursor_is_exclusive_and_forwarded_without_private_reads():
    store = Store(HTTPMockFixtureReceiptPage((receipt("hr", "v2"),)))
    page = service(store).list(
        DEV, limit=2, after_connection_id="hr", after_connection_revision="v1"
    )
    assert page.items[0].connection_revision == "v2"
    assert store.calls[0][1] == {
        "limit": 2,
        "after_connection_id": "hr",
        "after_connection_revision": "v1",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 51},
        {"limit": "2"},
        {"after_connection_id": "hr"},
        {"after_connection_revision": "v1"},
        {"after_connection_id": "", "after_connection_revision": "v1"},
        {"after_connection_id": "bad/" + PRIVATE, "after_connection_revision": "v1"},
        {"after_connection_id": 1, "after_connection_revision": "v1"},
    ],
)
def test_invalid_queries_do_not_touch_store(changes):
    store = Store()
    failure(lambda: service(store).list(DEV, **changes), "invalid_request")
    assert store.calls == []


def test_authorization_precedes_provider_attribute_inspection():
    class ForbiddenStore:
        @property
        def list_metadata(self):
            raise AssertionError("Provider was inspected")

    auth = Authorization()
    auth.failure = PermissionError(PRIVATE)
    failure(lambda: service(ForbiddenStore(), auth).list(DEV, limit=0), "forbidden")


@pytest.mark.parametrize(
    "scope,scopes",
    [
        (DEV, ()),
        (Scope(organization_id="foreign", environment_id="dev"), (DEV,)),
        (Scope(organization_id="acme", environment_id="prod"), (DEV,)),
        (Scope(organization_id="acme", environment_id="uat"), (DEV,)),
    ],
)
def test_scope_and_dev_enablement_gate_listing(scope, scopes):
    store = Store()
    failure(lambda: service(store, scopes=scopes).list(scope), "forbidden")
    assert not store.calls


def test_postlookup_revocation_releases_no_metadata():
    auth = Authorization()
    store = Store()
    store.on_list = lambda scope: setattr(auth, "failure", PermissionError(PRIVATE))
    failure(lambda: service(store, auth).list(DEV), "forbidden")
    assert len(store.calls) == 1


def test_identity_failure_classification_is_preserved():
    auth = Authorization()
    auth.failure = IdentityError("unauthenticated", PRIVATE)
    with pytest.raises(IdentityError) as error:
        service(authorization=auth).list(DEV)
    assert error.value.code == "unauthenticated"


@pytest.mark.parametrize(
    "page",
    [
        None,
        {},
        HTTPMockFixtureReceiptPage([receipt()]),
        HTTPMockFixtureReceiptPage((receipt(), receipt())),
        HTTPMockFixtureReceiptPage((receipt("z"), receipt("a"))),
        HTTPMockFixtureReceiptPage(
            (receipt(scope=Scope(organization_id="foreign", environment_id="dev")),)
        ),
        HTTPMockFixtureReceiptPage((receipt(stored_bytes=True),)),
        HTTPMockFixtureReceiptPage((receipt(fixture_digest=PRIVATE),)),
        HTTPMockFixtureReceiptPage((receipt(connection_id="bad/" + PRIVATE),)),
        HTTPMockFixtureReceiptPage((receipt(),), "hr", None),
        HTTPMockFixtureReceiptPage((receipt(),), None, "v1"),
        HTTPMockFixtureReceiptPage((), "hr", "v1"),
        HTTPMockFixtureReceiptPage((receipt(),), "other", "v1"),
        HTTPMockFixtureReceiptPage((receipt(),), "hr", PRIVATE + "/"),
    ],
)
def test_malformed_provider_pages_fail_private(page):
    store = Store()
    store.page = page
    failure(lambda: service(store).list(DEV, limit=1))


def test_cursor_outside_returned_page_is_rejected():
    failure(lambda: service().list(DEV, after_connection_id="hr", after_connection_revision="v1"))
    store = Store(HTTPMockFixtureReceiptPage((receipt(),), "hr", "v1"))
    failure(lambda: service(store).list(DEV, limit=2))


@pytest.mark.parametrize(
    "error,expected",
    [
        (HTTPMockStorageError("invalid_request"), "invalid_request"),
        (HTTPMockStorageError("conflict"), "unavailable"),
        (HTTPMockStorageError("limit_exceeded"), "unavailable"),
        (OSError(PRIVATE), "unavailable"),
        (PermissionError(PRIVATE), "unavailable"),
    ],
)
def test_private_provider_failures_are_normalized(error, expected):
    store = Store()
    store.failure = error
    failure(lambda: service(store).list(DEV), expected)


def test_legacy_store_without_listing_fails_unavailable():
    failure(lambda: service(object()).list(DEV))


def test_malformed_storage_error_code_cannot_execute_comparison():
    class HostileCode:
        def __eq__(self, other):
            raise RuntimeError(PRIVATE)

    store = Store()
    error = HTTPMockStorageError("unavailable")
    error.code = HostileCode()
    store.failure = error
    failure(lambda: service(store).list(DEV))
