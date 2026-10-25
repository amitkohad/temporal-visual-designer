"""Opaque attachments preserve immutable activation and actual Temporal execution pins."""

# ruff: noqa: F811
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
from nexusflow.tests.runtime.test_project_history_integration import approval_content
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_attachments_preserve_activation_receipts_execution_pins_and_replay(
    api, temporal_env, tmp_path
):
    _, backend, data, _ = api
    _, scope, _, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="evidence", correlation_id="evidence"
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
    journal = SQLiteDispatchJournal(tmp_path / "evidence-dispatch.db")

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

                async def publish(revision, key):
                    response = await client.post(
                        project + "/releases",
                        json={"source_revision": revision, "idempotency_key": key},
                    )
                    assert response.status_code == 201, response.text
                    return response.json()["release_id"]

                async def activate(release_id, operation, expected, evidence, action="activate"):
                    response = await client.post(
                        project + "/activation",
                        json={
                            "project_id": "hr",
                            "release_id": release_id,
                            "operation_id": operation,
                            "expected_revision": expected,
                            "configuration_revision": "cfg-1",
                            "action": action,
                            "evidence": evidence,
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["evidence"] == evidence
                    return response.json()

                async def start(key, release_id):
                    response = await client.post(
                        project + "/executions",
                        json={"workflow_id": "onboarding", "idempotency_key": key},
                    )
                    assert response.status_code == 202, response.text
                    assert response.json()["release_id"] == release_id
                    handle = runtime_client.get_workflow_handle(response.json()["execution_id"])
                    snapshot = await wait_state(handle, "waiting_approval", "approval")
                    assert snapshot.release_id == release_id
                    return handle

                a = await publish(1, "release-a")
                first = await activate(
                    a, "deploy-a", 0, {"change_request_number": "CR-1", "notes": None}
                )
                old = await start("old-a", a)
                changed = approval_content()
                changed["layout"] = {"version": "B"}
                changed["workflows"][0]["input_schema"]["description"] = "B"
                saved = await client.put(
                    project, json={"expected_revision": 1, "summary": "B", "content": changed}
                )
                assert saved.status_code == 200, saved.text
                b = await publish(2, "release-b")
                second = await activate(
                    b, "deploy-b", 1, {"change_request_number": "CR-2", "notes": None}
                )
                new_b = await start("new-b", b)
                attachments = project + "/activations/deploy-a/attachments"
                proof = b"<script>offline approval</script>\x00\xff"
                uploaded = await client.put(
                    attachments + "/approval",
                    params={"display_name": "Offline approval"},
                    content=proof,
                    headers={"content-type": "application/octet-stream"},
                )
                assert uploaded.status_code == 201, uploaded.text
                assert (await client.get(attachments)).json() == [uploaded.json()]
                assert (await client.get(attachments + "/approval")).content == proof
                retried = await activate(
                    a, "deploy-a", 0, {"change_request_number": "CR-1", "notes": None}
                )
                assert retried == first
                assert (await client.get(project + "/activation")).json() == second
                detail = (await client.get(project + "/activations/deploy-a")).json()
                assert detail["checksum"] == first["checksum"]
                for index, (handle, release_id) in enumerate(((old, a), (new_b, b))):
                    snapshot = await wait_state(handle, "waiting_approval")
                    assert snapshot.release_id == release_id
                    await handle.execute_update(
                        ProofWorkflow.approve,
                        ApprovalCommand(f"complete-{index}", "approval", True, snapshot.revision),
                    )
                    await handle.result()
                    assert (await handle.query(ProofWorkflow.snapshot)).release_id == release_id
                await Replayer(workflows=[ProofWorkflow]).replay_workflow(await old.fetch_history())
    finally:
        journal.close()
