"""Actual local CLI/socket/SQLite staging survives a full Temporal launcher restart."""

import json
import time

import pytest

from nexusflow.apps.local_bootstrap import TENANT_ID, USER_ID
from nexusflow.contracts import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant
from nexusflow.tests.api.test_project_package_acceptance import entries, repack
from nexusflow.tests.runtime.test_local_launcher_integration import _cli, _ports, _run, _serve

pytestmark = pytest.mark.integration


def test_real_local_package_intake_restart_keeps_unsigned_stage_non_executable(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    initialized = _run(state, "init")
    assert initialized.returncode == 0, initialized.stderr
    organization = json.loads(initialized.stdout)["organization_id"]
    issued = _run(state, "token")
    assert issued.returncode == 0, issued.stderr
    headers = {"authorization": "Bearer " + issued.stdout.strip()}
    dev = f"/api/v1/organizations/{organization}/environments/dev"
    uat = dev.removesuffix("dev") + "uat"
    uat_scope = Scope(organization_id=organization, environment_id="uat")
    repository = SQLiteIdentityRepository(state / "identity.db")
    repository.save_grant(
        Grant("uat-author", uat_scope, TENANT_ID, "user", USER_ID, "business_author"),
        expected_revision=0,
    )
    repository.close()
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    path = uat + "/package-imports/restart-a"
    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        activation = client.get(dev + "/projects/welcome/activation")
        assert activation.status_code == 200, activation.text
        release_id = activation.json()["release_id"]
        exported = client.get(dev + "/projects/welcome/releases/" + release_id + "/package")
        assert exported.status_code == 200, exported.text
        archive = exported.content
        receipt = client.put(path, content=archive, headers={"content-type": "application/zip"})
        assert receipt.status_code == 201, receipt.text
        assert receipt.json()["state"] == "staged"
        assert client.get(uat + "/projects").json() == []
        assert client.get(uat + "/projects/welcome/activation").json()["release_id"] is None
        assert client.get(dev + "/projects/welcome/activation").json() == activation.json()
        started = client.post(
            dev + "/projects/welcome/executions",
            json={"workflow_id": "welcome", "idempotency_key": "intake-proof", "input": {}},
        )
        assert started.status_code == 202, started.text
        execution_path = dev + "/projects/welcome/executions/" + started.json()["execution_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(execution_path)
            assert status.status_code == 200, status.text
            if status.json()["state"] == "completed":
                break
            time.sleep(0.1)
        else:
            pytest.fail("Original Dev release did not complete")
    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        assert client.get(path).json() == receipt.json()
        assert client.get(path + "/archive").content == archive
        retry = client.put(
            path, content=repack(entries(archive)), headers={"content-type": "application/zip"}
        )
        assert retry.status_code == 201, retry.text
        assert retry.json() == receipt.json()
        assert client.get(path + "/archive").content == archive
        assert client.get(execution_path).json()["state"] == "completed"
        assert client.get(dev + "/projects/welcome/activation").json() == activation.json()
        assert client.get(uat + "/projects").json() == []
        repository = SQLiteIdentityRepository(state / "identity.db")
        repository.revoke_grant(uat_scope, "uat-author", expected_revision=1)
        repository.close()
        assert client.get(path).status_code == 403
        assert client.get(path + "/archive").status_code == 403
        assert client.get(dev + "/projects/welcome").status_code == 200
    assert issued.stdout.strip() not in log.read_text(encoding="utf-8", errors="replace")
