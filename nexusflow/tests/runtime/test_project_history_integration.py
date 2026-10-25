# ruff: noqa: F811
"""Draft restoration cannot retarget real Temporal executions or activation."""

import json
from dataclasses import replace

import httpx
import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from nexusflow.apps.api import create_app
from nexusflow.apps.api_backend import TemporalRuntimeGateway
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import ApprovalCommand
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def approval_content():
    value = content()
    workflow = value["workflows"][0]
    workflow["nodes"].insert(
        1,
        {
            "node_id": "approval",
            "kind": "approval",
            "named_ports": {"inputs": ["in"], "outputs": ["out"]},
            "input_mapping": {},
        },
    )
    workflow["edges"] = [
        {
            "source_node_id": source,
            "source_port": "out",
            "target_node_id": target,
            "target_port": "in",
        }
        for source, target in (("start", "approval"), ("approval", "end"))
    ]
    return value


async def test_restore_draft_preserves_active_release_and_waiting_run_replay(
    api, temporal_env, tmp_path
):
    _, backend, data, _ = api
    _, scope, _, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="history", correlation_id="history"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    runtime_client = Client(
        temporal_env.client.service_client, namespace=placement.temporal_namespace
    )
    journal = SQLiteDispatchJournal(tmp_path / "history-dispatch.db")

    async def provider(requested):
        assert requested == placement
        return runtime_client

    app = create_app(replace(backend, runtime=TemporalRuntimeGateway(provider, journal)))
    project = BASE + "/projects/hr"
    try:
        async with Worker(
            runtime_client, task_queue=placement.task_queue, workflows=[ProofWorkflow]
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://local",
                headers={"authorization": "Bearer " + token()},
            ) as client:
                initial = await client.post(
                    BASE + "/projects",
                    json={"project_id": "hr", "name": "HR", "content": approval_content()},
                )
                assert initial.status_code == 201, initial.text
                released = await client.post(
                    project + "/releases",
                    json={"source_revision": 1, "idempotency_key": "history-A"},
                )
                assert released.status_code == 201, released.text
                release_a = released.json()
                activated = await client.post(
                    project + "/activation",
                    json={
                        "project_id": "hr",
                        "release_id": release_a["release_id"],
                        "operation_id": "history-activate-A",
                        "configuration_revision": "cfg-1",
                        "expected_revision": 0,
                    },
                )
                assert activated.status_code == 200, activated.text

                async def start(key):
                    response = await client.post(
                        project + "/executions",
                        json={"workflow_id": "onboarding", "idempotency_key": key},
                    )
                    assert response.status_code == 202, response.text
                    assert response.json()["release_id"] == release_a["release_id"]
                    handle = runtime_client.get_workflow_handle(response.json()["execution_id"])
                    await wait_state(handle, "waiting_approval", "approval")
                    return handle

                old = await start("before-restore")
                saved = await client.put(
                    project,
                    json={
                        "expected_revision": 1,
                        "summary": "New draft B",
                        "content": content("B"),
                    },
                )
                assert saved.status_code == 200, saved.text
                released_b = await client.post(
                    project + "/releases",
                    json={"source_revision": 2, "idempotency_key": "history-B"},
                )
                assert released_b.status_code == 201, released_b.text
                restored = await client.post(
                    project + "/restore",
                    json={
                        "source_revision": 1,
                        "expected_revision": 2,
                        "summary": "Restore A draft",
                    },
                )
                assert restored.status_code == 201, restored.text
                assert restored.json()["revision"] == 3
                assert restored.json()["content"] == initial.json()["content"]
                assert (await client.get(project + "/activation")).json() == activated.json()
                assert (await client.get(project + "?revision=2")).json() == saved.json()
                # Release publication remains idempotent and immutable after restore.
                assert (
                    await client.post(
                        project + "/releases",
                        json={"source_revision": 1, "idempotency_key": "history-A"},
                    )
                ).json() == release_a
                assert (
                    await client.post(
                        project + "/releases",
                        json={"source_revision": 2, "idempotency_key": "history-B"},
                    )
                ).json() == released_b.json()
                new = await start("after-restore")
                for index, handle in enumerate((old, new)):
                    snapshot = await wait_state(handle, "waiting_approval")
                    assert snapshot.release_id == release_a["release_id"]
                    await handle.execute_update(
                        ProofWorkflow.approve,
                        ApprovalCommand(
                            f"history-approval-{index}", "approval", True, snapshot.revision
                        ),
                    )
                    await handle.result()
                    final = await handle.query(ProofWorkflow.snapshot)
                    assert final.release_id == release_a["release_id"]
                    history = await handle.fetch_history()
                    assert json.loads(history.to_json())["events"]
                    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)
    finally:
        journal.close()
