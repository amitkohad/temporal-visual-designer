"""Signed frozen step input projection matches actual dispatched literal values."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio import activity
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from nexusflow.apps.api import create_app
from nexusflow.modules.activity_catalog.types import ActivityManifest
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import ActivityCall
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.tests.activity_catalog.test_validation import archive
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PAYLOAD
from nexusflow.tests.api.test_debug_step_inputs import (
    step_input_bundle,
    step_input_path,
    step_input_project,
    step_input_values,
)
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class CapturedLiteralInputs:
    def __init__(self):
        self.calls = []

    @activity.defn(name="nexusflow.proof.dispatch")
    async def dispatch(self, call: ActivityCall) -> str:
        self.calls.append(call)
        return '{"status":"complete"}'


async def test_frozen_step_input_survives_worker_outage_and_matches_dispatch(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, data, _ = api
    identity_store, scope, grant, _, token = data
    bundle = step_input_bundle()
    imported = backend.intake.put(scope, bundle, "publisher")
    entry = backend.catalog.publish(imported, bundle.archive, "publisher")
    project = step_input_project(entry.reference)
    literal = step_input_values()
    placement = backend.identity.placement(
        token(), scope, request_id="step-input", correlation_id="step-input"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "step-input-dispatch.db")
    sessions = SQLiteDebugSessionRepository(tmp_path / "step-input-sessions.db")
    forbidden = False
    runtime_access = []

    async def clients(requested):
        assert requested == placement
        assert not forbidden, "Input inspection must not access a runtime client"
        runtime_access.append(requested)
        return temporal

    configured = replace(
        backend, debug_sessions=sessions, debug_runtime=TemporalDebugRuntime(clients, journal)
    )
    effects = CapturedLiteralInputs()
    headers = {"authorization": "Bearer " + token()}
    path = BASE + "/projects/hr/debug-sessions"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured)),
            base_url="http://local-api",
            headers=headers,
        ) as client:
            created = await client.post(
                BASE + "/projects",
                json={"project_id": "hr", "name": "HR", "content": project},
            )
            assert created.status_code == 201, created.text
            async with Worker(
                temporal,
                task_queue=placement.task_queue,
                workflows=[ProofWorkflow],
                activities=[effects.dispatch],
            ):
                started = await client.post(path, json={**PAYLOAD, "input": {"other": "workflow"}})
                assert started.status_code == 202, started.text
                session = started.json()
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                paused = await wait_state(handle, "paused", "inspect")
                input_path = step_input_path(session)
                forbidden = True
                access_before = list(runtime_access)
                inspected = await client.get(input_path)
                assert inspected.status_code == 200, inspected.text
                assert inspected.headers["cache-control"] == "no-store"
                expected = inspected.json()
                assert expected["values"] == {"status": "ready", "rows": [{"name": "first"}]}
                assert expected["activity_ref"] == entry.reference.model_dump(mode="json")
                assert "other" not in expected["values"]
                for key in ("secret", "unclassified", "dynamic-private-key"):
                    assert literal[key] not in inspected.text
                assert "dynamic-private-key" not in inspected.text
                for key, value in literal["rows"][0].items():
                    if key != "name":
                        assert value not in inspected.text
                assert runtime_access == access_before
                assert await handle.query(ProofWorkflow.snapshot) == paused
                assert effects.calls == []

            # The worker is fully stopped and runtime access is prohibited. The
            # read must use only the saved plan and original immutable schemas.
            assert (await client.get(input_path)).json() == expected
            assert runtime_access == access_before
            descriptor = ActivityManifest.model_validate_json(bundle.manifest_json).descriptor
            changed_descriptor = descriptor.model_dump(mode="json")
            changed_descriptor["activity_ref"]["version"] = "2.0.0"
            changed_descriptor["input_schema"] = {
                "type": "object",
                "properties": {"secret": {"type": "string", "x-nexusflow-inspect": True}},
            }
            newer = validate_bundle(
                archive(
                    {"src/activity.py": b"def run(value):\n    return value\n"},
                    descriptor=changed_descriptor,
                )
            )
            newer_import = backend.intake.put(scope, newer, "publisher")
            newer_entry = backend.catalog.publish(newer_import, newer.archive, "publisher")
            saved = await client.put(
                BASE + "/projects/hr",
                json={
                    "expected_revision": 1,
                    "summary": "Different activity input classification",
                    "content": step_input_project(newer_entry.reference, {"secret": "new draft"}),
                },
            )
            assert saved.status_code == 200, saved.text
            assert (await client.get(input_path)).json() == expected
            assert runtime_access == access_before

            async with Worker(
                temporal,
                task_queue=placement.task_queue,
                workflows=[ProofWorkflow],
                activities=[effects.dispatch],
            ):
                assert await handle.query(ProofWorkflow.snapshot) == paused
                assert (await client.get(input_path)).json() == expected
                forbidden = False
                stepped = await client.post(
                    path + "/" + session["session_id"] + "/commands",
                    json={
                        "command_id": "dispatch-original-input",
                        "node_id": "inspect",
                        "action": "step_over",
                        "expected_revision": 0,
                    },
                )
                assert stepped.status_code == 200, stepped.text
                await wait_state(handle, "paused", "later")
                assert len(effects.calls) == 1
                assert json.loads(effects.calls[0].input_json) == literal
                assert json.loads(effects.calls[0].workflow_input_json) == {"other": "workflow"}
                forbidden = True
                assert (await client.get(input_path)).json() == expected
                forbidden = False
                finished = await client.post(
                    path + "/" + session["session_id"] + "/commands",
                    json={
                        "command_id": "finish-after-inspection",
                        "node_id": "later",
                        "action": "continue",
                        "expected_revision": 1,
                    },
                )
                assert finished.status_code == 200, finished.text
                await asyncio.wait_for(handle.result(), timeout=20)
            forbidden = True
            assert (await client.get(input_path)).json() == expected
            identity_store.revoke_grant(scope, grant.grant_id, expected_revision=1)
            assert (await client.get(input_path)).status_code == 403
            assert backend.activations.current(scope, "hr") is None
            assert len(effects.calls) == 1
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        sessions.close()
        journal.close()
