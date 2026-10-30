"""Private deterministic fixtures require no network, secrets or external store."""

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_scenarios import (
    StaticHTTPMockScenarios,
    checked_scenario,
    scenario_digest,
)
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.project_versioning.canonical import canonical

PRIVATE = "fixture-private-value"
SCOPE = Scope(organization_id="acme", environment_id="dev")


def scenario(**changes):
    result = HTTPMockScenario(
        scope=SCOPE,
        connection_id="hr-api",
        connection_revision="v1",
        expected_path="/employee?private=" + PRIVATE,
        expected_request=HTTPAttemptRequest(method="POST", body=PRIVATE.encode()),
        status_code=201,
        headers=(("content-type", "application/json"), ("x-private", PRIVATE)),
        body=b'{"value":"' + PRIVATE.encode() + b'"}',
    )
    return replace(result, **changes)


def failure(call, code="invalid_provider_result"):
    with pytest.raises(HTTPMockError) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.retryable is False
    assert caught.value.delivery_may_have_occurred is False
    assert caught.value.__suppress_context__
    assert PRIVATE not in str(caught.value) + repr(caught.value)


def test_snapshot_is_deep_independent_and_private():
    original = scenario()
    copied = checked_scenario(original)
    assert copied == original and copied is not original
    assert copied.scope is not original.scope
    assert copied.expected_request is not original.expected_request
    assert copied.headers is not original.headers
    assert PRIVATE not in repr(copied)
    with pytest.raises(FrozenInstanceError):
        copied.body = b"changed"
    object.__setattr__(original.scope, "environment_id", "changed")
    assert copied.scope.environment_id == "dev"
    object.__setattr__(original.scope, "environment_id", "dev")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "relative",
        "//other.example.com/",
        "/a#fragment",
        "/a\\b",
        "/a\n",
        "/%00",
        "/%zz",
        "/" + "a" * 4096,
    ],
)
def test_unsafe_or_noncanonical_path_is_rejected(path):
    failure(lambda: checked_scenario(scenario(expected_path=path)))


@pytest.mark.parametrize("path", ["/", "/employees", "/employees?q=a%20b", "/?a=1&a=2"])
def test_canonical_relative_path_is_supported(path):
    assert checked_scenario(scenario(expected_path=path)).expected_path == path


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": None},
        {"scope": Scope.model_construct(organization_id=PRIVATE, environment_id=1)},
        {"connection_id": None},
        {"connection_id": "bad/" + PRIVATE},
        {"connection_revision": ""},
        {"connection_revision": 1},
        {"expected_request": None},
        {"expected_request": HTTPAttemptRequest(method="post")},
        {"expected_request": HTTPAttemptRequest(body=b"GET body")},
        {"expected_request": HTTPAttemptRequest(method="POST", body=bytearray(b"bad"))},
        {"status_code": True},
        {"status_code": 199},
        {"status_code": 600},
        {"body": bytearray(b"bad")},
        {"headers": []},
        {"headers": (("Upper", PRIVATE),)},
        {"headers": (("x", "bad\r\n" + PRIVATE),)},
        {"headers": (("x", "\t" + PRIVATE),)},
        {"headers": (("x", "\u0100" + PRIVATE),)},
        {"headers": (("x", 1),)},
        {"headers": (("x",),)},
        {"headers": (("x", "a"),) * 101},
        {"headers": (["x", "a"],)},
        {"headers": ((1, "a"),)},
    ],
)
def test_malformed_fixture_is_rejected_without_private_error(changes, recwarn):
    failure(lambda: checked_scenario(scenario(**changes)))
    assert not recwarn.list


@pytest.mark.parametrize("status", [204, 304])
def test_bodyless_status_rejects_body(status):
    failure(lambda: checked_scenario(scenario(status_code=status)))
    assert checked_scenario(scenario(status_code=status, body=b"")).body == b""


def test_head_rejects_response_body():
    failure(lambda: checked_scenario(scenario(expected_request=HTTPAttemptRequest(method="HEAD"))))
    assert checked_scenario(scenario(expected_request=HTTPAttemptRequest(method="HEAD"), body=b""))


@pytest.mark.parametrize(
    "limits",
    [
        {"max_request_bytes": -1},
        {"max_request_bytes": True},
        {"max_response_bytes": 4194305},
        {"max_response_bytes": 1.0},
        {"max_header_bytes": 1023},
        {"max_header_bytes": 65537},
        {"max_header_bytes": True},
    ],
)
def test_invalid_limits_fail_closed(limits):
    failure(lambda: checked_scenario(scenario(), **limits))


def test_exact_request_response_and_header_limits():
    value = scenario(
        expected_request=HTTPAttemptRequest(method="POST", body=b"abc"),
        headers=(("x", "a" * 1019),),
        body=b"def",
    )
    assert checked_scenario(value, max_request_bytes=3, max_response_bytes=3, max_header_bytes=1024)
    failure(lambda: checked_scenario(value, max_request_bytes=2))
    failure(lambda: checked_scenario(value, max_response_bytes=2))
    failure(
        lambda: checked_scenario(
            replace(value, headers=(("x", "a" * 1020),)), max_header_bytes=1024
        )
    )


def test_digest_is_stable_versioned_and_binary_safe():
    value = scenario(
        expected_request=HTTPAttemptRequest(method="POST", body=bytes(range(256))),
        body=bytes(range(256)),
    )
    assert scenario_digest(value) == scenario_digest(checked_scenario(value))
    assert scenario_digest(value).startswith("sha256:") and len(scenario_digest(value)) == 71
    assert PRIVATE not in scenario_digest(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", Scope(organization_id="other", environment_id="dev")),
        ("scope", Scope(organization_id="acme", environment_id="uat")),
        ("connection_id", "other"),
        ("connection_revision", "v2"),
        ("expected_path", "/other"),
        ("expected_request", HTTPAttemptRequest(method="PUT", body=PRIVATE.encode())),
        ("expected_request", HTTPAttemptRequest(method="POST", body=b"other")),
        (
            "expected_request",
            HTTPAttemptRequest(method="POST", body=PRIVATE.encode(), content_type="text/plain"),
        ),
        ("status_code", 200),
        ("headers", (("x", "other"),)),
        ("body", b"other"),
    ],
)
def test_digest_changes_for_every_semantic_field(field, value):
    original = scenario()
    assert scenario_digest(original) != scenario_digest(replace(original, **{field: value}))


def test_digest_supports_configured_body_size_above_default():
    value = scenario(body=b"a" * 1048577)
    failure(lambda: checked_scenario(value))
    assert scenario_digest(value).startswith("sha256:")


@pytest.mark.asyncio
async def test_store_is_scoped_and_clones_each_lookup():
    original = scenario()
    other = scenario(scope=Scope(organization_id="other", environment_id="dev"))
    store = StaticHTTPMockScenarios((original, other))
    first = await store.get(SCOPE, "hr-api", "v1")
    second = await store.get(SCOPE, "hr-api", "v1")
    assert first == second == original and first is not second
    object.__setattr__(first.scope, "environment_id", "corrupted")
    object.__setattr__(first.expected_request, "body", b"corrupted")
    object.__setattr__(original, "body", b"corrupted")
    third = await store.get(SCOPE, "hr-api", "v1")
    assert third.scope.environment_id == "dev" and third.body != b"corrupted"
    assert third.expected_request.body == PRIVATE.encode()
    assert await store.get(Scope(organization_id="other", environment_id="dev"), "hr-api", "v1")
    assert (
        await store.get(Scope(organization_id="acme", environment_id="uat"), "hr-api", "v1") is None
    )
    assert await store.get(SCOPE, "hr-api", "v2") is None


@pytest.mark.asyncio
async def test_concurrent_lookups_return_independent_snapshots():
    store = StaticHTTPMockScenarios((scenario(),))
    results = await asyncio.gather(*(store.get(SCOPE, "hr-api", "v1") for _ in range(20)))
    assert all(result == results[0] for result in results)
    assert len({id(result) for result in results}) == 20
    assert len({id(result.scope) for result in results}) == 20


@pytest.mark.parametrize(
    "scenarios,limits",
    [
        ([], {}),
        ((scenario(), scenario()), {}),
        ((scenario(),), {"max_scenarios": 0}),
        ((scenario(),), {"max_scenarios": True}),
        ((scenario(),), {"max_scenarios": 1001}),
        ((scenario(),), {"max_total_bytes": 0}),
        ((scenario(),), {"max_total_bytes": True}),
        ((scenario(),), {"max_total_bytes": 67108865}),
        ((scenario(), scenario(connection_revision="v2")), {"max_scenarios": 1}),
        ((scenario(), None), {}),
    ],
)
def test_invalid_catalog_configuration_or_duplicates_are_rejected(scenarios, limits):
    failure(lambda: StaticHTTPMockScenarios(scenarios, **limits))


def test_aggregate_encoded_budget_is_exact():
    # Identity calculation is independent of private implementation helpers.
    import base64

    value = scenario()
    definition = {
        "schema_version": "0.1",
        "kind": "http-mock-scenario",
        "scope": {"organization_id": "acme", "environment_id": "dev"},
        "connection_id": value.connection_id,
        "connection_revision": value.connection_revision,
        "expected_path": value.expected_path,
        "expected_method": value.expected_request.method,
        "expected_content_type": value.expected_request.content_type,
        "expected_body_base64": base64.b64encode(value.expected_request.body).decode("ascii"),
        "status_code": value.status_code,
        "headers": value.headers,
        "response_body_base64": base64.b64encode(value.body).decode("ascii"),
    }
    size = len(canonical(definition).encode("utf-8"))
    assert StaticHTTPMockScenarios((value,), max_total_bytes=size)
    failure(lambda: StaticHTTPMockScenarios((value,), max_total_bytes=size - 1))
    failure(
        lambda: StaticHTTPMockScenarios(
            (value, scenario(connection_revision="v2")), max_total_bytes=size
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,connection,revision",
    [
        (None, "hr-api", "v1"),
        (SCOPE, "bad/" + PRIVATE, "v1"),
        (SCOPE, "hr-api", True),
    ],
)
async def test_invalid_lookup_is_private_invalid_request(scope, connection, revision):
    store = StaticHTTPMockScenarios(())
    with pytest.raises(HTTPMockError) as caught:
        await store.get(scope, connection, revision)
    assert caught.value.code == "invalid_request"
    assert PRIVATE not in repr(caught.value) + str(caught.value)
