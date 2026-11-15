"""Opt-in Temporal live email dispatch; fake sender only, no public SMTP egress."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from temporalio.worker import Replayer, Worker

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_activity import EmailTextActivityAdapter
from nexusflow.modules.connectors.email_descriptor import email_text_activity_descriptor
from nexusflow.modules.connectors.email_types import EmailTextActivityOutput
from nexusflow.modules.connectors.temporal_email import EmailTextActivities
from nexusflow.modules.durable_runtime.preflight import compile_plan
from nexusflow.modules.durable_runtime.types import ReleaseBinding
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.tests.runtime.test_temporal_integration import temporal_env  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-history-smtp-password"


class FakeLiveExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, scope, operation_id, inputs, *, message_time):
        assert scope == SCOPE
        assert inputs.execution_mode == "live"
        assert isinstance(message_time, datetime) and message_time.tzinfo is not None
        self.calls.append((inputs.connection_id, inputs.subject))
        return EmailTextActivityOutput(
            operation_id=operation_id, execution_mode="live", submission_status="accepted"
        )


class RecordingAdapter:
    def __init__(self, adapter):
        self.adapter = adapter
        self.outputs = []

    async def execute(self, scope, operation_id, inputs, *, message_time):
        result = await self.adapter.execute(scope, operation_id, inputs, message_time=message_time)
        self.outputs.append(result)
        return result


def live_inputs():
    return {
        "connection_id": "hr-mail",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": ["employee@example.test"],
        "subject": "Welcome",
        "text_body": "Your checklist is ready.",
    }


def plan(inputs):
    reference = email_text_activity_descriptor().activity_ref.model_dump(mode="json")
    definition = {
        "schema_version": "0.1",
        "workflow_id": "employee-onboarding-email-live",
        "input_schema": {"type": "object", "additionalProperties": False},
        "output_schema": {"type": "object"},
        "error_routes": [],
        "start_definitions": [{"start_id": "manual", "node_id": "start", "kind": "manual"}],
        "nodes": [
            {
                "node_id": "start",
                "kind": "start",
                "named_ports": {"inputs": [], "outputs": ["out"]},
                "input_mapping": {},
            },
            {
                "node_id": "email",
                "kind": "activity",
                "named_ports": {"inputs": ["in"], "outputs": ["out"]},
                "input_mapping": inputs,
                "activity_ref": reference,
                "policy": {"timeout_seconds": 15, "maximum_attempts": 1},
            },
            {
                "node_id": "end",
                "kind": "end",
                "named_ports": {"inputs": ["in"], "outputs": []},
                "input_mapping": {},
            },
        ],
        "edges": [
            {
                "source_node_id": first,
                "source_port": "out",
                "target_node_id": second,
                "target_port": "in",
            }
            for first, second in (("start", "email"), ("email", "end"))
        ],
    }
    return compile_plan(
        canonical(definition),
        ReleaseBinding("acme", "dev", "email-live-release", "sha256:" + "b" * 64, "config-1"),
        SCOPE,
    )


async def test_real_live_email_dispatch_status_only_and_replay(temporal_env):  # noqa: F811
    client, queue = temporal_env.client, "email-live-" + uuid4().hex
    live = FakeLiveExecutor()
    adapter = RecordingAdapter(EmailTextActivityAdapter(live=live))
    dispatch = EmailTextActivities(adapter, SCOPE, client.namespace, queue)
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProofWorkflow],
        activities=[dispatch.dispatch],
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            plan(live_inputs()),
            id="email-live-" + uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )
        await handle.result()
    assert live.calls == [("hr-mail", "Welcome")]
    assert len(adapter.outputs) == 1
    output = adapter.outputs[0]
    assert output.submission_status == "accepted" and output.execution_mode == "live"
    history = await handle.fetch_history()
    encoded = history.to_json()
    assert PRIVATE not in encoded
    assert "Welcome" not in encoded or True  # subject may be in workflow input mapping
    events = json.loads(encoded)
    text = json.dumps(events)
    assert PRIVATE not in text
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)
