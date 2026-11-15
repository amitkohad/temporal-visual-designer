"""Pure Temporal live email invocation checks; no SMTP or server needed."""

import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.email_descriptor import email_text_activity_descriptor
from nexusflow.modules.connectors.temporal_email_policy import prepare_email_text_call
from nexusflow.modules.connectors.temporal_email_types import (
    EmailTextActivityContext,
    EmailTextDispatchError,
)
from nexusflow.modules.durable_runtime.types import ActivityCall, ReleaseBinding

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "private-email-body-value"
CONTEXT = EmailTextActivityContext(
    namespace="default",
    workflow_namespace="default",
    task_queue="email-worker",
    workflow_type="NexusFlowProofV1",
    workflow_id="execution-1",
    workflow_run_id="temporal-run-1",
    activity_type="nexusflow.proof.dispatch",
    activity_id="notify",
    attempt=1,
)


def call(**changes):
    reference = email_text_activity_descriptor().activity_ref
    body = {
        "connection_id": "hr-mail",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": ["employee@example.test"],
        "subject": "Welcome",
        "text_body": PRIVATE,
    }
    value = ActivityCall(
        binding=ReleaseBinding("acme", "dev", "release-1", "sha256:" + "a" * 64, "config-1"),
        node_id="notify",
        operation_id="execution-1:notify",
        activity_id=reference.activity_id,
        activity_version=reference.version,
        artifact_digest=reference.artifact_digest,
        input_json=json.dumps(body),
        workflow_input_json=json.dumps({"unused_private_business_input": PRIVATE}),
    )
    return replace(value, **changes)


def prepare(value=None, context=CONTEXT, scope=SCOPE, namespace="default", queue="email-worker"):
    return prepare_email_text_call(value or call(), context, scope, namespace, queue)


def test_live_mode_uses_email_operation_kind():
    live = prepare()
    assert live.inputs.execution_mode == "live"
    assert live.inputs.fixture_digest is None
    assert TypeAdapter(Identifier).validate_python(live.operation_id) == live.operation_id
    assert live.operation_id.startswith("email-")
    assert PRIVATE not in repr(live)


def test_retry_attempt_keeps_live_identity_but_new_run_changes_it():
    original = prepare()
    retry = prepare(context=replace(CONTEXT, attempt=2))
    assert retry.operation_id == original.operation_id
    changed = prepare(context=replace(CONTEXT, workflow_run_id="temporal-run-2"))
    assert changed.operation_id != original.operation_id


def test_mock_mode_and_wrong_descriptor_are_forbidden():
    reference = email_text_activity_descriptor().activity_ref
    mock_body = {
        "connection_id": "hr-mail",
        "connection_revision": "v1",
        "execution_mode": "mock",
        "fixture_digest": "sha256:" + "b" * 64,
        "to": ["employee@example.test"],
        "subject": "Welcome",
        "text_body": PRIVATE,
    }
    mock = call(input_json=json.dumps(mock_body))
    with pytest.raises(EmailTextDispatchError) as caught:
        prepare(mock)
    assert caught.value.code == "forbidden"
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    wrong = call(activity_id="nexus.http.json", artifact_digest=reference.artifact_digest)
    with pytest.raises(EmailTextDispatchError) as error:
        prepare(wrong)
    assert error.value.code == "forbidden"
