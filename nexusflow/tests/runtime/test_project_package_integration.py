"""Package transfer is passive while real Temporal runs preserve release pins."""

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


async def test_package_export_validation_cannot_activate_or_retarget_a_waiting_execution(
    api, temporal_env, tmp_path
):
    _, backend, data, _ = api
    _, scope, _, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="package", correlation_id="package"
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
    journal = SQLiteDispatchJournal(tmp_path / "package-dispatch.db")

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

                async def publish_activate(revision, key, expected):
                    response = await client.post(
                        project + "/releases",
                        json={"source_revision": revision, "idempotency_key": key},
                    )
                    assert response.status_code == 201, response.text
                    release = response.json()["release_id"]
                    activated = await client.post(
                        project + "/activation",
                        json={
                            "project_id": "hr",
                            "release_id": release,
                            "operation_id": key,
                            "expected_revision": expected,
                            "configuration_revision": "cfg-1",
                        },
                    )
                    assert activated.status_code == 200, activated.text
                    return release

                async def start(key, release):
                    started = await client.post(
                        project + "/executions",
                        json={"workflow_id": "onboarding", "idempotency_key": key},
                    )
                    assert started.status_code == 202, started.text
                    assert started.json()["release_id"] == release
                    handle = runtime_client.get_workflow_handle(started.json()["execution_id"])
                    assert (
                        await wait_state(handle, "waiting_approval", "approval")
                    ).release_id == release
                    return handle

                a = await publish_activate(1, "a", 0)
                old = await start("old-a", a)
                original = await client.get(project + "/releases/" + a + "/package")
                assert original.status_code == 200, original.text
                checked = await client.post(
                    BASE + "/packages/validation",
                    content=original.content,
                    headers={"content-type": "application/zip"},
                )
                assert checked.status_code == 200, checked.text
                assert checked.json()["release_id"] == a
                assert (await client.get(project + "/activation")).json()["revision"] == 1
                changed = approval_content()
                changed["layout"] = {"version": "B"}
                changed["workflows"][0]["input_schema"]["description"] = "B"
                saved = await client.put(
                    project, json={"expected_revision": 1, "summary": "B", "content": changed}
                )
                assert saved.status_code == 200, saved.text
                b = await publish_activate(2, "b", 1)
                new = await start("new-b", b)
                repeated = await client.get(project + "/releases/" + a + "/package")
                assert repeated.content == original.content
                assert repeated.headers["x-package-digest"] == original.headers["x-package-digest"]
                checked_again = await client.post(
                    BASE + "/packages/validation",
                    content=original.content,
                    headers={"content-type": "application/zip"},
                )
                assert checked_again.json() == checked.json()
                assert (await client.get(project + "/activation")).json()["release_id"] == b
                for index, (handle, release) in enumerate(((old, a), (new, b))):
                    snapshot = await wait_state(handle, "waiting_approval")
                    assert snapshot.release_id == release
                    await handle.execute_update(
                        ProofWorkflow.approve,
                        ApprovalCommand(f"complete-{index}", "approval", True, snapshot.revision),
                    )
                    await handle.result()
                    assert (await handle.query(ProofWorkflow.snapshot)).release_id == release
                await Replayer(workflows=[ProofWorkflow]).replay_workflow(await old.fetch_history())
    finally:
        journal.close()
