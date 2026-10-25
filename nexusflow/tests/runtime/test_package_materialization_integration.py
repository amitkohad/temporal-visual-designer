"""Real Temporal execution after passive Dev to UAT package materialization."""

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
from nexusflow.contracts.models import Scope
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.package_promotion.package_imports import SQLitePackageImportRepository
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content
from nexusflow.tests.runtime.test_temporal_integration import temporal_env  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_materialized_uat_release_runs_only_after_explicit_activation(
    api, temporal_env, tmp_path
):
    _, backend, data, _ = api
    identity_repo, _, grant, _, token = data
    scope = Scope(organization_id="acme", environment_id="uat")
    identity_repo.save_grant(replace(grant, scope=scope), expected_revision=0)
    placement = backend.identity.placement(
        token(), scope, request_id="materialize", correlation_id="materialize"
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
    journal = SQLiteDispatchJournal(tmp_path / "materialized-dispatch.db")
    imports = SQLitePackageImportRepository(str(tmp_path / "materialized-packages.db"))

    async def provider(requested):
        assert requested == placement
        return runtime_client

    app = create_app(
        replace(backend, package_imports=imports, runtime=TemporalRuntimeGateway(provider, journal))
    )
    uat = BASE.replace("/dev", "/uat")
    try:
        async with Worker(
            runtime_client, task_queue=placement.task_queue, workflows=[ProofWorkflow]
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://local",
                headers={"authorization": "Bearer " + token()},
            ) as client:
                created = await client.post(
                    BASE + "/projects",
                    json={"project_id": "hr", "name": "HR", "content": content()},
                )
                assert created.status_code == 201, created.text
                published = await client.post(
                    BASE + "/projects/hr/releases",
                    json={"source_revision": 1, "idempotency_key": "source"},
                )
                assert published.status_code == 201, published.text
                source_id = published.json()["release_id"]
                exported = await client.get(BASE + f"/projects/hr/releases/{source_id}/package")
                assert exported.status_code == 200, exported.text
                staged = await client.put(
                    uat + "/package-imports/promote",
                    content=exported.content,
                    headers={"content-type": "application/zip"},
                )
                assert staged.status_code == 201, staged.text
                imported = await client.post(
                    uat + "/package-imports/promote/materializations",
                    json={
                        "operation_id": "install",
                        "expected_project_revision": 0,
                        "configuration_revision": "cfg-1",
                    },
                )
                assert imported.status_code == 201, imported.text
                target_id = imported.json()["release_id"]
                assert target_id != source_id
                assert backend.activations.current(scope, "hr") is None
                rejected = await client.post(
                    uat + "/projects/hr/executions",
                    json={"workflow_id": "onboarding", "idempotency_key": "before"},
                )
                assert rejected.status_code == 404, rejected.text
                activated = await client.post(
                    uat + "/projects/hr/activation",
                    json={
                        "project_id": "hr",
                        "release_id": target_id,
                        "operation_id": "activate",
                        "expected_revision": 0,
                        "configuration_revision": "cfg-1",
                    },
                )
                assert activated.status_code == 200, activated.text
                started = await client.post(
                    uat + "/projects/hr/executions",
                    json={"workflow_id": "onboarding", "idempotency_key": "after"},
                )
                assert started.status_code == 202, started.text
                assert started.json()["release_id"] == target_id
                handle = runtime_client.get_workflow_handle(started.json()["execution_id"])
                await handle.result()
                snapshot = await handle.query(ProofWorkflow.snapshot)
                assert snapshot.state == "completed" and snapshot.release_id == target_id
                await Replayer(workflows=[ProofWorkflow]).replay_workflow(
                    await handle.fetch_history()
                )
    finally:
        imports.close()
        journal.close()
