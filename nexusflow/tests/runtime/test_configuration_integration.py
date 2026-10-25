"""Actual Temporal execution with a durable destination configuration revision."""

from dataclasses import replace

import httpx
import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from nexusflow.apps.api import create_app
from nexusflow.apps.api_backend import TemporalRuntimeGateway
from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.package_promotion.configurations import SQLiteConfigurationRepository
from nexusflow.modules.package_promotion.package_imports import SQLitePackageImportRepository
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.api.test_configuration_acceptance import SCHEMA, VALUES
from nexusflow.tests.identity.test_service import identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content
from nexusflow.tests.runtime.test_temporal_integration import temporal_env  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_target_configuration_materialize_activate_execute_and_replay(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, data, _ = api
    repository, _, grant, _, token = data
    scope = Scope(organization_id="acme", environment_id="uat")
    repository.save_grant(replace(grant, scope=scope), expected_revision=0)
    placement = backend.identity.placement(
        token(),
        scope,
        request_id="configuration-placement",
        correlation_id="configuration-placement",
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
    journal = SQLiteDispatchJournal(tmp_path / "configuration-dispatch.db")
    configs = SQLiteConfigurationRepository(tmp_path / "runtime-configurations.db")
    imports = SQLitePackageImportRepository(tmp_path / "runtime-imports.db")

    async def client_provider(requested):
        assert requested == placement
        return runtime_client

    app = create_app(
        replace(
            backend,
            configurations=configs,
            package_imports=imports,
            runtime=TemporalRuntimeGateway(client_provider, journal),
        )
    )
    target = BASE.replace("/dev", "/uat")
    try:
        async with Worker(
            runtime_client, task_queue=placement.task_queue, workflows=[ProofWorkflow]
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://local-api",
                headers={"authorization": "Bearer " + token()},
            ) as client:
                project = content()
                project["configuration_schema"] = SCHEMA
                result = await client.post(
                    BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project}
                )
                assert result.status_code == 201, result.text
                published = await client.post(
                    BASE + "/projects/hr/releases",
                    json={"source_revision": 1, "idempotency_key": "source"},
                )
                assert published.status_code == 201, published.text
                exported = await client.get(
                    BASE + "/projects/hr/releases/" + published.json()["release_id"] + "/package"
                )
                assert exported.status_code == 200, exported.text
                staged = await client.put(
                    target + "/package-imports/transfer",
                    content=exported.content,
                    headers={"content-type": "application/zip"},
                )
                assert staged.status_code == 201, staged.text
                command = {
                    "operation_id": "materialize",
                    "expected_project_revision": 0,
                    "configuration_revision": "target-cfg",
                }
                assert (
                    await client.post(
                        target + "/package-imports/transfer/materializations", json=command
                    )
                ).status_code == 422
                repository.save_grant(
                    replace(grant, scope=scope, role="organization_administrator"),
                    expected_revision=1,
                )
                configured = await client.put(
                    target + "/projects/hr/configurations/target-cfg", json={"values": VALUES}
                )
                assert configured.status_code == 201, configured.text
                repository.save_grant(replace(grant, scope=scope), expected_revision=2)
                installed = await client.post(
                    target + "/package-imports/transfer/materializations", json=command
                )
                assert installed.status_code == 201, installed.text
                assert backend.activations.current(scope, "hr") is None
                release_id = installed.json()["release_id"]
                activated = await client.post(
                    target + "/projects/hr/activation",
                    json={
                        "project_id": "hr",
                        "release_id": release_id,
                        "configuration_revision": "target-cfg",
                        "operation_id": "activate",
                        "expected_revision": 0,
                    },
                )
                assert activated.status_code == 200, activated.text
                assert activated.json()["configuration_revision"] == "target-cfg"
                started = await client.post(
                    target + "/projects/hr/executions",
                    json={"workflow_id": "onboarding", "idempotency_key": "configured-run"},
                )
                assert started.status_code == 202, started.text
                execution_id = started.json()["execution_id"]
                handle = runtime_client.get_workflow_handle(execution_id)
                await handle.result()
                status = await client.get(target + "/projects/hr/executions/" + execution_id)
                assert status.status_code == 200, status.text
                assert status.json()["state"] == "completed"
                await Replayer(workflows=[ProofWorkflow]).replay_workflow(
                    await handle.fetch_history()
                )
                assert VALUES["destination"] not in status.text + activated.text + configured.text
    finally:
        imports.close()
        configs.close()
        journal.close()
