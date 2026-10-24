"""Signed HTTP API → actual registered Temporal namespace → persisted run status."""

from dataclasses import replace

import httpx
import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.worker import Worker

from nexusflow.apps.api import create_app
from nexusflow.apps.api_backend import TemporalRuntimeGateway
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content
from nexusflow.tests.runtime.test_temporal_integration import temporal_env  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_http_release_start_and_status_use_actual_temporal_placement(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, identity_data, _ = api
    _, scope, _, _, token = identity_data
    placement = backend.identity.placement(
        token(), scope, request_id="placement", correlation_id="placement"
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
    journal = SQLiteDispatchJournal(tmp_path / "dispatch.db")

    async def client_provider(requested):
        assert requested == placement
        return runtime_client

    gateway = TemporalRuntimeGateway(client_provider, journal)
    app = create_app(replace(backend, runtime=gateway))
    try:
        async with Worker(
            runtime_client, task_queue=placement.task_queue, workflows=[ProofWorkflow]
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://local-api",
                headers={"authorization": "Bearer " + token()},
            ) as client:
                response = await client.post(
                    BASE + "/projects",
                    json={"project_id": "hr", "name": "HR", "content": content()},
                )
                assert response.status_code == 201, response.text
                response = await client.post(
                    BASE + "/projects/hr/releases",
                    json={"source_revision": 1, "idempotency_key": "release"},
                )
                assert response.status_code == 201, response.text
                release_id = response.json()["release_id"]
                response = await client.post(
                    BASE + "/projects/hr/activation",
                    json={
                        "project_id": "hr",
                        "release_id": release_id,
                        "operation_id": "activate",
                        "configuration_revision": "cfg-1",
                        "expected_revision": 0,
                    },
                )
                assert response.status_code == 200, response.text
                payload = {
                    "workflow_id": "onboarding",
                    "idempotency_key": "run",
                    "input": {"private": "not-in-response"},
                }
                started = await client.post(BASE + "/projects/hr/executions", json=payload)
                assert started.status_code == 202, started.text
                execution = started.json()["execution_id"]
                await runtime_client.get_workflow_handle(execution).result()
                status = await client.get(BASE + "/projects/hr/executions/" + execution)
                assert status.status_code == 200, status.text
                assert status.json()["state"] == "completed"
                assert status.json()["release_id"] == release_id
                assert "not-in-response" not in status.text + started.text
                assert (
                    await client.post(BASE + "/projects/hr/executions", json=payload)
                ).json() == started.json()
                assert (
                    await client.get(
                        BASE.replace("/dev", "/uat") + "/projects/hr/executions/" + execution
                    )
                ).status_code == 403
    finally:
        journal.close()
