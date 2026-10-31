"""Actual launcher fixture administration; no HTTP mock workflow dispatch claim."""

import asyncio
import json
import time

import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.tests.runtime.test_local_launcher_integration import _cli, _ports, _run, _serve

pytestmark = pytest.mark.integration
PRIVATE = "synthetic-fixture-never-in-output"
MARKER = "http-mock-fixtures.json"
DATABASE = "http-mock-fixtures.db"
GRANT = "local-mock-fixture-admin"
BODY = {
    "method": "POST",
    "path": "/employees?private=" + PRIVATE,
    "json_body": {"employee": PRIVATE},
    "status_code": 201,
    "response_json_body": {"accepted": PRIVATE},
}


def _identity(state, scope):
    repository = SQLiteIdentityRepository(state / "identity.db")
    try:
        grants = repository.grants(scope)
        placement = next(
            item for item in repository.environments(scope.organization_id) if item.scope == scope
        )
        assert all(
            not repository.grants(scope.model_copy(update={"environment_id": environment}))
            for environment in ("uat", "prod")
        )
        return grants, placement
    finally:
        repository.close()


def _completed(client, path):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        if response.json()["state"] == "completed":
            return response.json()
        time.sleep(0.1)
    pytest.fail("Ordinary welcome workflow did not complete")


async def _replay(port, namespace, execution_id):
    client = await Client.connect(f"127.0.0.1:{port}", namespace=namespace)
    handle = client.get_workflow_handle(execution_id)
    async with asyncio.timeout(30):
        await handle.result()
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


def test_explicit_local_fixture_administration_survives_restart_without_restoring_grants(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    initialized = _run(state, "init")
    assert initialized.returncode == 0, initialized.stderr
    organization = json.loads(initialized.stdout)["organization_id"]
    scope = Scope(organization_id=organization, environment_id="dev")
    base = f"/api/v1/organizations/{organization}/environments/dev"
    listing = base + "/mock-fixtures"
    fixture = base + "/connections/hr/revisions/v1/mock-fixture"
    token = _run(state, "token")
    assert token.returncode == 0, token.stderr
    headers = {"authorization": "Bearer " + token.stdout.strip()}
    signing_key = (state / "private-key.pem").read_bytes()
    assert not (state / MARKER).exists() and not (state / DATABASE).exists()
    initial_grants, placement = _identity(state, scope)
    assert [grant.grant_id for grant in initial_grants] == ["local-author"]
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"

    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        assert client.get(listing).status_code == 403
        assert client.put(fixture, json=BODY).status_code == 403
        project = client.get(base + "/projects/welcome")
        activation = client.get(base + "/projects/welcome/activation")
        assert project.status_code == activation.status_code == 200
    assert not (state / MARKER).exists() and not (state / DATABASE).exists()

    enabled = _run(state, "enable-mock-fixtures")
    assert enabled.returncode == 0, enabled.stderr
    metadata = json.loads(enabled.stdout)
    assert metadata["mock_fixtures"] == "enabled"
    assert metadata["administrator_role"] == "organization_administrator"
    assert metadata["organization_id"] == organization and metadata["environment_id"] == "dev"
    assert PRIVATE not in enabled.stdout and token.stdout.strip() not in enabled.stdout
    marker = (state / MARKER).read_bytes()
    assert (state / DATABASE).is_file()
    grants, _ = _identity(state, scope)
    assert {(grant.grant_id, grant.role) for grant in grants} == {
        ("local-author", "business_author"),
        (GRANT, "organization_administrator"),
    }
    assert (state / "private-key.pem").read_bytes() == signing_key

    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        # The pre-enable token resolves fresh server-owned grants.
        saved = client.put(fixture, json=BODY)
        assert saved.status_code == 201, saved.text
        first_metadata = saved.json()
        assert set(first_metadata) == {
            "scope",
            "connection_id",
            "connection_revision",
            "fixture_digest",
            "stored_bytes",
        }
        second = client.put(fixture.replace("/revisions/v1/", "/revisions/v2/"), json=BODY)
        assert second.status_code == 201, second.text
        page = client.get(listing, params={"limit": 1})
        assert page.status_code == 200, page.text
        assert page.json()["items"] == [first_metadata]
        cursor = {
            "after_connection_id": page.json()["next_after_connection_id"],
            "after_connection_revision": page.json()["next_after_connection_revision"],
        }
        assert cursor == {"after_connection_id": "hr", "after_connection_revision": "v1"}
        assert PRIVATE not in saved.text + second.text + page.text
        assert client.get(base + "/projects/welcome").json() == project.json()
        assert client.get(base + "/projects/welcome/activation").json() == activation.json()
        accepted = client.post(
            base + "/projects/welcome/executions",
            json={"workflow_id": "welcome", "idempotency_key": "fixtures-normal-run"},
        )
        assert accepted.status_code == 202, accepted.text
        execution_id = accepted.json()["execution_id"]
        execution_path = base + "/projects/welcome/executions/" + execution_id
        _completed(client, execution_path)
        asyncio.run(_replay(temporal_port, placement.temporal_namespace, execution_id))
        for environment in ("uat", "prod"):
            assert client.get(listing.replace("/dev/", f"/{environment}/")).status_code == 403

    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        assert client.get(fixture).json() == first_metadata
        assert client.put(fixture, json=BODY).json() == first_metadata
        resumed = client.get(listing, params=cursor)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json() == {
            "items": [second.json()],
            "next_after_connection_id": None,
            "next_after_connection_revision": None,
        }
        assert client.get(execution_path).json()["state"] == "completed"
        contender = _run(state, "enable-mock-fixtures")
        assert contender.returncode != 0

    identities = SQLiteIdentityRepository(state / "identity.db")
    try:
        grant = next(item for item in identities.grants(scope) if item.grant_id == GRANT)
        identities.revoke_grant(scope, GRANT, expected_revision=grant.revision)
    finally:
        identities.close()
    repeated = _run(state, "enable-mock-fixtures")
    assert repeated.returncode == 0, repeated.stderr
    assert (state / MARKER).read_bytes() == marker
    assert (state / "private-key.pem").read_bytes() == signing_key
    grants, _ = _identity(state, scope)
    assert [grant.grant_id for grant in grants] == ["local-author"]
    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        for response in (client.get(listing), client.get(fixture), client.put(fixture, json=BODY)):
            assert response.status_code == 403, response.text
        assert client.get(base + "/projects/welcome").json() == project.json()
        assert client.get(base + "/projects/welcome/activation").json() == activation.json()
        assert client.get(execution_path).json()["state"] == "completed"
    logs = log.read_text(encoding="utf-8", errors="replace")
    assert PRIVATE not in logs and token.stdout.strip() not in logs and "PRIVATE KEY" not in logs


@pytest.mark.parametrize("damage", ["marker", "database"])
def test_broken_fixture_profile_fails_before_launcher_runtime_resources(tmp_path, damage):
    executable = _cli()
    state = tmp_path / "state"
    assert _run(state, "init").returncode == 0
    enabled = _run(state, "enable-mock-fixtures")
    assert enabled.returncode == 0, enabled.stderr
    if damage == "marker":
        (state / MARKER).write_text('{"profile":"' + PRIVATE + '"}', encoding="utf-8")
    else:
        (state / DATABASE).unlink()
    files = {path.name: path.read_bytes() for path in state.iterdir() if path.is_file()}
    api_port, temporal_port = _ports()
    failed = _run(
        state,
        "serve",
        "--api-port",
        str(api_port),
        "--temporal-port",
        str(temporal_port),
        "--temporal-cli",
        executable,
    )
    assert failed.returncode != 0
    assert PRIVATE not in failed.stdout + failed.stderr
    assert not (state / "runtime-identity.json").exists()
    assert not (state / "temporal.db").exists() and not (state / "dispatch.db").exists()
    assert {path.name: path.read_bytes() for path in state.iterdir() if path.is_file()} == files
