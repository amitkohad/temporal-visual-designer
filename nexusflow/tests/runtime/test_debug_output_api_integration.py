"""Signed schema-redacted output inspection against actual activity history."""

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
from nexusflow.tests.api.test_debug_outputs import output_bundle, output_project
from nexusflow.tests.api.test_debug_sessions import PAYLOAD
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class PrivateOutputs:
    def __init__(self):
        self.calls = []

    @activity.defn(name="nexusflow.proof.dispatch")
    async def dispatch(self, call: ActivityCall) -> str:
        self.calls.append(call)
        return json.dumps(
            {
                "status": "ready",
                "secret": "never-show-secret-value",
                "unclassified": "never-show-unclassified-value",
                "private-dynamic-key": "never-show-dynamic-value",
                "rows": [{"name": "first", "password": "never-show-password-value"}],
            }
        )


async def test_signed_output_redaction_keeps_original_schema_through_restart_and_replay(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, data, _ = api
    identity_store, scope, grant, _, token = data
    bundle = output_bundle()
    imported = backend.intake.put(scope, bundle, "publisher")
    entry = backend.catalog.publish(imported, bundle.archive, "publisher")
    project = output_project(entry.reference)
    placement = backend.identity.placement(
        token(), scope, request_id="outputs", correlation_id="outputs"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "output-dispatch.db")
    sessions = SQLiteDebugSessionRepository(tmp_path / "output-sessions.db")

    async def clients(requested):
        assert requested == placement
        return temporal

    configured = replace(
        backend, debug_sessions=sessions, debug_runtime=TemporalDebugRuntime(clients, journal)
    )
    effects = PrivateOutputs()
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
                started = await client.post(path, json=PAYLOAD)
                assert started.status_code == 202, started.text
                session = started.json()
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                await wait_state(handle, "paused", "inspect")
                session_path = path + "/" + session["session_id"]
                output_path = session_path + "/steps/inspect/output"
                assert (await client.get(output_path)).status_code == 409
                step = await client.post(
                    session_path + "/commands",
                    json={
                        "command_id": "step-output",
                        "node_id": "inspect",
                        "action": "step_over",
                        "expected_revision": 0,
                    },
                )
                assert step.status_code == 200, step.text
                paused = await wait_state(handle, "paused", "later")
                inspected = await client.get(output_path)
                assert inspected.status_code == 200, inspected.text
                assert inspected.headers["cache-control"] == "no-store"
                expected = inspected.json()
                assert expected["values"] == {"status": "ready", "rows": [{"name": "first"}]}
                for hidden in (
                    "never-show-secret-value",
                    "never-show-unclassified-value",
                    "private-dynamic-key",
                    "never-show-dynamic-value",
                    "never-show-password-value",
                ):
                    assert hidden not in inspected.text
                assert expected["activity_ref"] == entry.reference.model_dump(mode="json")
                assert await handle.query(ProofWorkflow.snapshot) == paused
                assert len(effects.calls) == 1

                # A new catalog version and draft may expose a formerly private
                # field; the original session must keep its sealed old schema.
                descriptor = ActivityManifest.model_validate_json(
                    bundle.manifest_json
                ).descriptor.model_dump(mode="json")
                descriptor["activity_ref"]["version"] = "2.0.0"
                descriptor["output_schema"] = {
                    "type": "object",
                    "properties": {"secret": {"type": "string", "x-nexusflow-inspect": True}},
                }
                newer = validate_bundle(
                    archive(
                        {"src/activity.py": b"def run(value):\n    return value\n"},
                        descriptor=descriptor,
                    )
                )
                newer_import = backend.intake.put(scope, newer, "publisher")
                newer_entry = backend.catalog.publish(newer_import, newer.archive, "publisher")
                saved = await client.put(
                    BASE + "/projects/hr",
                    json={
                        "expected_revision": 1,
                        "summary": "New output classification in another revision",
                        "content": output_project(newer_entry.reference),
                    },
                )
                assert saved.status_code == 200, saved.text
                assert (await client.get(output_path)).json() == expected

            async with Worker(
                temporal,
                task_queue=placement.task_queue,
                workflows=[ProofWorkflow],
                activities=[effects.dispatch],
            ):
                assert await handle.query(ProofWorkflow.snapshot) == paused
                assert (await client.get(output_path)).json() == expected
                assert len(effects.calls) == 1
                finish = await client.post(
                    session_path + "/commands",
                    json={
                        "command_id": "finish-output",
                        "node_id": "later",
                        "action": "continue",
                        "expected_revision": 1,
                    },
                )
                assert finish.status_code == 200, finish.text
                await asyncio.wait_for(handle.result(), timeout=20)
                assert (await client.get(output_path)).json() == expected
                identity_store.revoke_grant(scope, grant.grant_id, expected_revision=1)
                assert (await client.get(output_path)).status_code == 403
                assert backend.activations.current(scope, "hr") is None
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        sessions.close()
        journal.close()
