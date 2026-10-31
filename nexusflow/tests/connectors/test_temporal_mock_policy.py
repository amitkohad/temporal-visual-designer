"""Pure Temporal mock invocation checks; no activity context, DNS or server is needed."""

import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.temporal_mock_policy import prepare_http_mock_call
from nexusflow.modules.connectors.temporal_mock_types import (
    HTTPMockActivityContext,
    HTTPMockDispatchError,
)
from nexusflow.modules.durable_runtime.types import ActivityCall, ReleaseBinding

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "private-workflow-value"
CONTEXT = HTTPMockActivityContext(
    namespace="default",
    workflow_namespace="default",
    task_queue="mock-worker",
    workflow_type="NexusFlowProofV1",
    workflow_id="execution-1",
    workflow_run_id="temporal-run-1",
    activity_type="nexusflow.proof.dispatch",
    activity_id="request",
    attempt=1,
)


def call(**changes):
    reference = http_json_activity_descriptor().activity_ref
    value = ActivityCall(
        binding=ReleaseBinding("acme", "dev", "release-1", "sha256:" + "a" * 64, "config-1"),
        node_id="request",
        operation_id="execution-1:request",
        activity_id=reference.activity_id,
        activity_version=reference.version,
        artifact_digest=reference.artifact_digest,
        input_json=json.dumps(
            {
                "connection_id": "hr",
                "connection_revision": "v1",
                "execution_mode": "mock",
                "fixture_digest": "sha256:" + "b" * 64,
                "method": "POST",
                "path": "/employee?private=" + PRIVATE,
                "json_body": {"employee": PRIVATE},
            }
        ),
        workflow_input_json=json.dumps({"unused_private_business_input": PRIVATE}),
    )
    return replace(value, **changes)


def prepare(value=None, context=CONTEXT, scope=SCOPE, namespace="default", queue="mock-worker"):
    return prepare_http_mock_call(value or call(), context, scope, namespace, queue)


def failure(value=None, *, code="invalid_request", **kwargs):
    with pytest.raises(HTTPMockDispatchError) as caught:
        prepare(value, **kwargs)
    assert caught.value.code == code
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


def test_valid_call_yields_private_bound_mock_input_and_safe_operation_identifier():
    result = prepare()
    assert result.scope == SCOPE and result.inputs.execution_mode == "mock"
    assert result.inputs.json_body == {"employee": PRIVATE}
    assert TypeAdapter(Identifier).validate_python(result.operation_id) == result.operation_id
    assert result.operation_id.startswith("http-") and len(result.operation_id) == 69
    assert ":" not in result.operation_id and PRIVATE not in repr(result)


def test_retry_attempt_does_not_change_identity_but_new_run_does():
    original = prepare()
    assert prepare(context=replace(CONTEXT, attempt=42)).operation_id == original.operation_id
    assert (
        prepare(context=replace(CONTEXT, workflow_run_id="next-run")).operation_id
        != original.operation_id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_id", "release-2"),
        ("release_digest", "sha256:" + "c" * 64),
        ("configuration_revision", "config-2"),
    ],
)
def test_release_pins_change_operation_identity(field, value):
    selected = call()
    changed = replace(selected, binding=replace(selected.binding, **{field: value}))
    assert prepare(changed).operation_id != prepare(selected).operation_id


def test_node_workflow_scope_queue_and_validated_input_change_identity():
    original = prepare().operation_id
    renamed = replace(CONTEXT, workflow_id="execution-2")
    assert (
        prepare(call(operation_id="execution-2:request"), context=renamed).operation_id != original
    )
    assert (
        prepare(
            call(node_id="next", operation_id="execution-1:next"),
            context=replace(CONTEXT, activity_id="next"),
        ).operation_id
        != original
    )
    assert (
        prepare(
            context=replace(CONTEXT, task_queue="another-worker"), queue="another-worker"
        ).operation_id
        != original
    )
    modified = json.loads(call().input_json)
    modified["json_body"] = {"employee": "different"}
    assert prepare(call(input_json=json.dumps(modified))).operation_id != original
    selected = call()
    assert (
        prepare(
            replace(selected, binding=replace(selected.binding, environment_id="sandbox")),
            scope=Scope(organization_id="acme", environment_id="sandbox"),
        ).operation_id
        != original
    )


def test_canonical_input_order_and_unused_workflow_inputs_do_not_change_identity():
    selected = call()
    reordered = json.dumps(json.loads(selected.input_json), sort_keys=True, indent=2)
    assert (
        prepare(call(input_json=reordered, workflow_input_json='{"different":true}')).operation_id
        == prepare(selected).operation_id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "other"),
        ("workflow_namespace", "other"),
        ("task_queue", "other"),
        ("workflow_type", "OtherWorkflow"),
        ("activity_type", "other.dispatch"),
        ("activity_id", "other"),
    ],
)
def test_wrong_temporal_context_is_forbidden(field, value):
    failure(context=replace(CONTEXT, **{field: value}), code="forbidden")


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", None),
        ("workflow_namespace", False),
        ("task_queue", "bad\nqueue"),
        ("workflow_type", 1),
        ("workflow_id", ""),
        ("workflow_run_id", "x" * 256),
        ("activity_type", " bad "),
        ("activity_id", "\x00"),
        ("attempt", True),
        ("attempt", 0),
        ("attempt", 2**31),
    ],
)
def test_malformed_temporal_context_is_invalid(field, value):
    failure(context=replace(CONTEXT, **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("activity_id", "other.http"),
        ("activity_version", "9.0.0"),
        ("artifact_digest", "sha256:" + "c" * 64),
        ("operation_id", "wrong:operation"),
    ],
)
def test_wrong_reference_or_legacy_operation_is_forbidden(field, value):
    failure(call(**{field: value}), code="forbidden")


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_id", "bad release"),
        ("configuration_revision", False),
        ("release_digest", "private-not-a-digest"),
        ("organization_id", None),
    ],
)
def test_malformed_release_binding_is_invalid(field, value):
    selected = call()
    failure(replace(selected, binding=replace(selected.binding, **{field: value})))


@pytest.mark.parametrize(
    "field,value",
    [("environment_id", "prod"), ("organization_id", "other"), ("runtime_version", "proof.v2")],
)
def test_wrong_release_scope_and_runtime_are_forbidden(field, value):
    selected = call()
    failure(
        replace(selected, binding=replace(selected.binding, **{field: value})), code="forbidden"
    )


@pytest.mark.parametrize("field", ["input_json", "workflow_input_json"])
@pytest.mark.parametrize(
    "value",
    [
        "[]",
        "null",
        "{",
        '{"a":1,"a":2}',
        '{"nested":{"x":1,"x":2}}',
        '{"n":NaN}',
        '{"n":Infinity}',
        '{"n":1e999}',
        '"' + PRIVATE + '"',
        " " * 65537,
    ],
    ids=[
        "array",
        "null",
        "broken",
        "duplicate",
        "nested-duplicate",
        "nan",
        "infinity",
        "overflow",
        "string",
        "oversized",
    ],
)
def test_json_must_be_bounded_unique_finite_objects(field, value):
    failure(call(**{field: value}))


def test_body_and_total_json_tree_depth_are_bounded():
    deep = {}
    for _ in range(34):
        deep = {"child": deep}
    for field in ("input_json", "workflow_input_json"):
        failure(call(**{field: json.dumps(deep)}))
        failure(call(**{field: json.dumps({"wide": list(range(10001))})}))


def test_live_mode_is_rejected_before_any_adapter_can_be_called():
    value = json.loads(call().input_json)
    value["execution_mode"] = "live"
    value.pop("fixture_digest")
    failure(call(input_json=json.dumps(value)), code="forbidden")


@pytest.mark.parametrize("field", ["scope", "operation_id", "headers", "credentials"])
def test_business_input_cannot_inject_context_or_credentials(field):
    value = json.loads(call().input_json)
    value[field] = PRIVATE
    failure(call(input_json=json.dumps(value)))


def test_malformed_native_dataclasses_and_scope_have_no_serializer_warnings(recwarn):
    failure(replace(call(), binding=None))
    failure(context=None)
    failure(scope=Scope.model_construct(organization_id=False, environment_id="dev"))
    assert not list(recwarn)


@pytest.mark.parametrize(
    "namespace,queue",
    [
        ("bad namespace", "mock-worker"),
        ("", "mock-worker"),
        ("default", " bad "),
        ("default", "x" * 256),
    ],
)
def test_worker_destination_configuration_is_validated(namespace, queue):
    failure(namespace=namespace, queue=queue)
