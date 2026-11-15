"""Pure Temporal live/mock HTTP JSON invocation checks; no DNS or server needed."""

import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.temporal_http_policy import prepare_http_json_call
from nexusflow.modules.connectors.temporal_http_types import (
    HTTPJSONActivityContext,
    HTTPJSONDispatchError,
)
from nexusflow.modules.durable_runtime.types import ActivityCall, ReleaseBinding

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "private-workflow-value"
CONTEXT = HTTPJSONActivityContext(
    namespace="default",
    workflow_namespace="default",
    task_queue="http-worker",
    workflow_type="NexusFlowProofV1",
    workflow_id="execution-1",
    workflow_run_id="temporal-run-1",
    activity_type="nexusflow.proof.dispatch",
    activity_id="request",
    attempt=1,
)


def call(*, mode="mock", **changes):
    reference = http_json_activity_descriptor().activity_ref
    body = {
        "connection_id": "hr",
        "connection_revision": "v1",
        "execution_mode": mode,
        "method": "POST",
        "path": "/employee?private=" + PRIVATE,
        "json_body": {"employee": PRIVATE},
    }
    if mode == "mock":
        body["fixture_digest"] = "sha256:" + "b" * 64
    value = ActivityCall(
        binding=ReleaseBinding("acme", "dev", "release-1", "sha256:" + "a" * 64, "config-1"),
        node_id="request",
        operation_id="execution-1:request",
        activity_id=reference.activity_id,
        activity_version=reference.version,
        artifact_digest=reference.artifact_digest,
        input_json=json.dumps(body),
        workflow_input_json=json.dumps({"unused_private_business_input": PRIVATE}),
    )
    return replace(value, **changes)


def prepare(value=None, context=CONTEXT, scope=SCOPE, namespace="default", queue="http-worker"):
    return prepare_http_json_call(value or call(), context, scope, namespace, queue)


def failure(value=None, *, code="invalid_request", **kwargs):
    with pytest.raises(HTTPJSONDispatchError) as caught:
        prepare(value, **kwargs)
    assert caught.value.code == code
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


def test_mock_and_live_modes_use_distinct_operation_kinds():
    mock = prepare(call(mode="mock"))
    live = prepare(call(mode="live"))
    assert mock.inputs.execution_mode == "mock" and live.inputs.execution_mode == "live"
    assert mock.operation_id != live.operation_id
    assert TypeAdapter(Identifier).validate_python(live.operation_id) == live.operation_id
    assert live.operation_id.startswith("http-") and PRIVATE not in repr(live)


def test_retry_attempt_keeps_live_identity_but_new_run_changes_it():
    original = prepare(call(mode="live"))
    assert (
        prepare(call(mode="live"), context=replace(CONTEXT, attempt=42)).operation_id
        == original.operation_id
    )
    assert (
        prepare(
            call(mode="live"), context=replace(CONTEXT, workflow_run_id="next-run")
        ).operation_id
        != original.operation_id
    )


def test_live_forbids_fixture_digest_and_mock_requires_it():
    live = json.loads(call(mode="live").input_json)
    live["fixture_digest"] = "sha256:" + "b" * 64
    # HTTPJSONActivityInput already rejects the combination; policy maps that to invalid_request.
    failure(call(mode="live", input_json=json.dumps(live)))
    mock = json.loads(call(mode="mock").input_json)
    mock.pop("fixture_digest")
    failure(call(mode="mock", input_json=json.dumps(mock)))


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "other"),
        ("workflow_type", "OtherWorkflow"),
        ("activity_type", "other.dispatch"),
    ],
)
def test_wrong_temporal_context_is_forbidden(field, value):
    failure(call(mode="live"), context=replace(CONTEXT, **{field: value}), code="forbidden")


def test_wrong_activity_reference_is_forbidden():
    failure(call(mode="live", activity_id="other.http"), code="forbidden")
