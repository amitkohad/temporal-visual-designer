"""Private administration boundaries with actual local storage and hostile ports."""

import asyncio
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_admin import HTTPMockFixtureService
from nexusflow.modules.connectors.mock_admin_types import (
    HTTPMockFixtureAdminError,
    HTTPMockFixtureMetadata,
    HTTPMockFixtureWrite,
)
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.modules.connectors.mock_scenarios import scenario_digest
from nexusflow.modules.connectors.mock_storage_codec import encode_scenario
from nexusflow.modules.connectors.mock_storage_types import (
    HTTPMockFixtureReceipt,
    HTTPMockStorageError,
)
from nexusflow.modules.connectors.mock_types import HTTPMockError
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.canonical import canonical

PRIVATE = "never-return-fixture-content"
DEV = Scope(organization_id="acme", environment_id="dev")


def payload(**changes):
    return HTTPMockFixtureWrite.model_validate(
        {
            "method": "POST",
            "path": "/" + PRIVATE,
            "json_body": {"z": PRIVATE, "a": [True, 1, None, "é"]},
            "status_code": 201,
            "response_json_body": {"value": PRIVATE},
            **changes,
        }
    )


class Authorization:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.fail_after = None

    def authorize(self, scope, permission, project_id):
        self.calls.append((scope, permission, project_id))
        if self.failure is not None:
            raise self.failure
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise PermissionError(PRIVATE)
        return "admin-1"


class Store:
    def __init__(self):
        self.scenario = None
        self.calls = []
        self.put_error = None
        self.get_error = None
        self.put_result = None
        self.on_put = lambda value: None
        self.on_get = lambda scope: None

    def put(self, value):
        self.calls.append("put")
        if self.put_error:
            raise self.put_error
        self.scenario = value
        receipt = HTTPMockFixtureReceipt(
            value.scope.model_copy(deep=True),
            value.connection_id,
            value.connection_revision,
            scenario_digest(value),
            len(encode_scenario(value).encode()),
        )
        self.on_put(value)
        return receipt if self.put_result is None else self.put_result(receipt)

    async def get(self, scope, connection_id, revision):
        self.calls.append("get")
        if self.get_error:
            raise self.get_error
        self.on_get(scope)
        return self.scenario


def service(store=None, authorization=None, scopes=(DEV,)):
    return HTTPMockFixtureService(store or Store(), authorization or Authorization(), scopes)


def failure(call, code):
    with pytest.raises(HTTPMockFixtureAdminError) as error:
        call()
    assert error.value.code == code
    assert PRIVATE not in str(error.value) + repr(error.value)
    assert error.value.__suppress_context__


def read(subject, scope=DEV, connection="hr", revision="r1"):
    return asyncio.run(subject.get(scope, connection, revision))


def test_actual_store_canonical_roundtrip_receipt_and_restart(tmp_path):
    database = tmp_path / "fixtures.db"
    original = payload()
    auth = Authorization()
    store = SQLiteHTTPMockScenarios(database)
    subject = service(store, auth)
    try:
        result = subject.put(DEV, "hr", "r1", original)
        assert type(result) is HTTPMockFixtureMetadata
        assert result == subject.put(DEV, "hr", "r1", original)
        assert read(subject) == result
        stored = asyncio.run(store.get(DEV, "hr", "r1"))
        assert stored.expected_request.body == canonical(original.json_body).encode()
        assert stored.body == canonical(original.response_json_body).encode()
        assert stored.headers == ()
        assert result.fixture_digest == scenario_digest(stored)
        assert result.stored_bytes == len(encode_scenario(stored).encode())
        assert PRIVATE not in result.model_dump_json() + repr(result)
        assert set(result.model_dump()) == {
            "scope",
            "connection_id",
            "connection_revision",
            "fixture_digest",
            "stored_bytes",
        }
        assert all(
            permission == "connection.manage" and resource == "connections"
            for _, permission, resource in auth.calls
        )
        assert len(auth.calls) == 6
    finally:
        store.close()
    reopened = SQLiteHTTPMockScenarios(database)
    try:
        assert read(service(reopened)) == result
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "method,status", [("GET", 200), ("HEAD", 200), ("POST", 204), ("POST", 304)]
)
def test_empty_request_and_response_bytes_are_preserved(method, status):
    port = Store()
    subject = service(port)
    subject.put(
        DEV,
        "hr",
        "r1",
        payload(method=method, json_body=None, status_code=status, response_json_body=None),
    )
    assert port.scenario.expected_request.body == b""
    assert port.scenario.body == b""
    assert read(subject).fixture_digest == scenario_digest(port.scenario)


def test_actual_store_immutable_conflict_and_quota(tmp_path):
    store = SQLiteHTTPMockScenarios(tmp_path / "fixtures.db", max_scenarios_per_scope=1)
    subject = service(store)
    try:
        first = subject.put(DEV, "hr", "r1", payload())
        assert subject.put(DEV, "hr", "r1", payload()) == first
        failure(lambda: subject.put(DEV, "hr", "r1", payload(status_code=202)), "conflict")
        failure(lambda: subject.put(DEV, "hr", "r2", payload()), "limit_exceeded")
        assert read(subject) == first
    finally:
        store.close()


@pytest.mark.parametrize(
    "scope",
    [
        Scope(organization_id="acme", environment_id="uat"),
        Scope(organization_id="acme", environment_id="prod"),
        Scope(organization_id="foreign", environment_id="dev"),
    ],
)
def test_scope_denial_precedes_store_access(scope):
    port, auth = Store(), Authorization()
    subject = service(port, auth)
    failure(lambda: subject.put(scope, "hr", "r1", payload()), "forbidden")
    failure(lambda: read(subject, scope), "forbidden")
    assert len(auth.calls) == 2 and port.calls == []


def test_empty_enabled_scope_configuration_denies_before_provider():
    port = Store()
    subject = service(port, scopes=())
    failure(lambda: subject.put(DEV, "hr", "r1", payload()), "forbidden")
    failure(lambda: read(subject), "forbidden")
    assert port.calls == []


@pytest.mark.parametrize(
    "scopes",
    [
        None,
        [],
        [DEV],
        (None,),
        (DEV, DEV),
        (Scope(organization_id="acme", environment_id="uat"),),
        (Scope(organization_id="acme", environment_id="prod"),),
        (Scope.model_construct(organization_id=PRIVATE, environment_id=1),),
    ],
)
def test_invalid_configuration_has_fixed_private_failure(scopes):
    with pytest.raises(ValueError) as error:
        service(scopes=scopes)
    assert str(error.value) == "Invalid HTTP mock fixture administration configuration"
    assert PRIVATE not in repr(error.value)


def test_configuration_is_snapshotted_as_immutable_identity():
    configured = DEV.model_copy(deep=True)
    subject = service(scopes=(configured,))
    object.__setattr__(configured, "organization_id", "changed")
    assert subject.put(DEV, "hr", "r1", payload()).scope == DEV
    failure(lambda: read(subject, configured), "forbidden")


@pytest.mark.parametrize(
    "connection,revision", [("bad/" + PRIVATE, "r1"), ("hr", ""), (True, "r1"), ("hr", 1)]
)
def test_invalid_keys_are_private_without_provider_access(connection, revision):
    port = Store()
    subject = service(port)
    failure(lambda: subject.put(DEV, connection, revision, payload()), "invalid_request")
    failure(lambda: read(subject, connection=connection, revision=revision), "invalid_request")
    assert port.calls == []


@pytest.mark.parametrize(
    "error,expected",
    [(PermissionError(PRIVATE), "forbidden"), (RuntimeError(PRIVATE), "unavailable")],
)
def test_fresh_authorization_failure_precedes_provider(error, expected):
    port, auth = Store(), Authorization()
    auth.failure = error
    subject = service(port, auth)
    failure(lambda: subject.put(DEV, "hr", "r1", payload()), expected)
    failure(lambda: read(subject), expected)
    assert port.calls == []


@pytest.mark.parametrize("code", ["unauthenticated", "forbidden"])
def test_identity_failure_retains_global_http_identity_mapping(code):
    port, auth = Store(), Authorization()
    original = IdentityError(code, PRIVATE)
    auth.failure = original
    subject = service(port, auth)
    for operation in (lambda: subject.put(DEV, "hr", "r1", payload()), lambda: read(subject)):
        with pytest.raises(IdentityError) as error:
            operation()
        assert error.value is original
    assert port.calls == []


def test_write_and_read_recheck_permission_after_provider_return():
    port, auth = Store(), Authorization()
    subject = service(port, auth)
    auth.fail_after = 1
    failure(lambda: subject.put(DEV, "hr", "r1", payload()), "forbidden")
    assert port.calls == ["put"]  # Accepted write may persist even if response is denied.
    auth.calls.clear()
    failure(lambda: read(subject), "forbidden")
    assert port.calls == ["put", "get"]


def test_missing_fixture_rechecks_permission_before_not_found():
    auth = Authorization()
    subject = service(authorization=auth)
    failure(lambda: read(subject), "not_found")
    assert len(auth.calls) == 2
    auth.calls.clear()
    auth.fail_after = 1
    failure(lambda: read(subject), "forbidden")


@pytest.mark.parametrize("code", ["invalid_request", "conflict", "limit_exceeded", "unavailable"])
def test_storage_codes_are_closed_and_private(code):
    port = Store()
    port.put_error = HTTPMockStorageError(code)
    failure(lambda: service(port).put(DEV, "hr", "r1", payload()), code)


def test_mutated_storage_error_cannot_spoof_an_authorization_code():
    port = Store()
    port.put_error = HTTPMockStorageError("unavailable")
    port.put_error.code = "forbidden"
    failure(lambda: service(port).put(DEV, "hr", "r1", payload()), "unavailable")


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError(PRIVATE),
        PermissionError(PRIVATE),
        HTTPMockError("forbidden"),
        ValueError(PRIVATE),
    ],
)
def test_provider_exceptions_never_become_public_payload(error):
    port = Store()
    port.put_error = port.get_error = error
    subject = service(port)
    failure(lambda: subject.put(DEV, "hr", "r1", payload()), "unavailable")
    failure(lambda: read(subject), "unavailable")


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": Scope(organization_id="foreign", environment_id="dev")},
        {"scope": Scope.model_construct(organization_id=1, environment_id="dev")},
        {"connection_id": "other"},
        {"connection_revision": "other"},
        {"fixture_digest": "sha256:" + "0" * 64},
        {"fixture_digest": PRIVATE},
        {"stored_bytes": True},
        {"stored_bytes": 0},
        {"stored_bytes": 12582913},
    ],
)
def test_provider_receipt_must_match_derived_metadata(changes):
    port = Store()
    port.put_result = lambda receipt: replace(receipt, **changes)
    failure(lambda: service(port).put(DEV, "hr", "r1", payload()), "unavailable")


def test_arbitrary_provider_receipt_is_rejected():
    port = Store()
    port.put_result = lambda receipt: {"private": PRIVATE}
    failure(lambda: service(port).put(DEV, "hr", "r1", payload()), "unavailable")


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": Scope(organization_id="foreign", environment_id="dev")},
        {"connection_id": "other"},
        {"connection_revision": "other"},
        {"status_code": True},
        {"body": bytearray(PRIVATE.encode())},
    ],
)
def test_returned_scenario_is_strict_and_bound_to_route(changes):
    port = Store()
    subject = service(port)
    subject.put(DEV, "hr", "r1", payload())
    port.scenario = replace(port.scenario, **changes)
    failure(lambda: read(subject), "unavailable")


def test_original_payload_and_returned_metadata_have_no_store_aliases():
    port = Store()
    subject = service(port)
    original = payload()
    result = subject.put(DEV, "hr", "r1", original)
    original.json_body["z"] = "changed"
    original.response_json_body["value"] = "changed"
    object.__setattr__(result.scope, "organization_id", "changed")
    assert PRIVATE.encode() in port.scenario.expected_request.body
    assert PRIVATE.encode() in port.scenario.body
    assert port.scenario.scope == DEV
    assert read(subject).scope == DEV


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "GET"},
        {"method": "invalid"},
        {"path": "//" + PRIVATE},
        {"json_body": []},
        {"json_body": {"value": float("nan")}},
        {"status_code": True},
        {"status_code": 199},
        {"response_json_body": []},
    ],
)
def test_tampered_constructed_payload_is_revalidated(changes):
    original = payload()
    values = original.model_dump()
    values.update(changes)
    forged = HTTPMockFixtureWrite.model_construct(**values)
    port = Store()
    failure(lambda: service(port).put(DEV, "hr", "r1", forged), "invalid_request")
    assert port.calls == []


def test_read_provider_cancellation_is_propagated():
    port = Store()
    port.get_error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        read(service(port))


def test_closed_actual_store_maps_to_private_unavailable(tmp_path):
    store = SQLiteHTTPMockScenarios(tmp_path / "f.db")
    subject = service(store)
    store.close()
    failure(lambda: subject.put(DEV, "hr", "r1", payload()), "unavailable")
    failure(lambda: read(subject), "unavailable")
