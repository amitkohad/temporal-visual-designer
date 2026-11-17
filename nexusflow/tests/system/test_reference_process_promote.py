"""SC-01 package promote: Dev export → UAT stage/materialize/activate (+ optional Prod evidence)."""

import pytest

from nexusflow.tests.system.helpers import (
    _cli,
    _initialized,
    _ports,
    _serve,
    create_publish_activate,
    env_base,
    grant_author,
)

pytestmark = pytest.mark.integration


def test_purchase_requisition_promotes_to_uat_and_optionally_prod_with_evidence(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    organization, headers = _initialized(state)
    grant_author(state, organization, "uat", "uat-author")
    grant_author(state, organization, "prod", "prod-support")
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    project_id = "purchase-promote"
    with _serve(state, executable, api_port, temporal_port, log) as client:
        project_path, _workflow, release_id = create_publish_activate(
            client,
            organization=organization,
            template="purchase-requisition",
            headers=headers,
            project_id=project_id,
        )
        exported = client.get(
            project_path + "/releases/" + release_id + "/package", headers=headers
        )
        assert exported.status_code == 200, exported.text
        archive = exported.content
        assert exported.headers["x-package-digest"]

        uat = env_base(organization, "uat")
        stage_id = "purchase-uat-import"
        staged = client.put(
            uat + "/package-imports/" + stage_id,
            headers={**headers, "content-type": "application/zip"},
            content=archive,
        )
        assert staged.status_code == 201, staged.text
        assert staged.json()["state"] == "staged"
        assert client.get(uat + "/projects", headers=headers).json() == []

        materialized = client.post(
            uat + f"/package-imports/{stage_id}/materializations",
            headers=headers,
            json={
                "operation_id": "purchase-uat-materialize",
                "expected_project_revision": 0,
                "configuration_revision": "local-v1",
            },
        )
        assert materialized.status_code == 201, materialized.text
        uat_release = materialized.json()["release_id"]
        assert uat_release != release_id
        assert materialized.json()["state"] == "materialized"
        assert (
            client.get(uat + f"/projects/{project_id}/activation", headers=headers).json()[
                "release_id"
            ]
            is None
        )

        activated = client.post(
            uat + f"/projects/{project_id}/activation",
            headers=headers,
            json={
                "project_id": project_id,
                "release_id": uat_release,
                "operation_id": "purchase-uat-activate",
                "configuration_revision": "local-v1",
                "expected_revision": 0,
            },
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["release_id"] == uat_release

        # Optional Prod path with change-request evidence + opaque attachment.
        prod = env_base(organization, "prod")
        prod_stage = "purchase-prod-import"
        staged_prod = client.put(
            prod + "/package-imports/" + prod_stage,
            headers={**headers, "content-type": "application/zip"},
            content=archive,
        )
        assert staged_prod.status_code == 201, staged_prod.text
        prod_materialized = client.post(
            prod + f"/package-imports/{prod_stage}/materializations",
            headers=headers,
            json={
                "operation_id": "purchase-prod-materialize",
                "expected_project_revision": 0,
                "configuration_revision": "local-v1",
            },
        )
        assert prod_materialized.status_code == 201, prod_materialized.text
        prod_release = prod_materialized.json()["release_id"]
        prod_activated = client.post(
            prod + f"/projects/{project_id}/activation",
            headers=headers,
            json={
                "project_id": project_id,
                "release_id": prod_release,
                "operation_id": "purchase-prod-activate",
                "configuration_revision": "local-v1",
                "expected_revision": 0,
                "evidence": {
                    "change_request_number": "CR-REF-001",
                    "notes": "Reference-process system acceptance",
                },
            },
        )
        assert prod_activated.status_code == 200, prod_activated.text
        assert prod_activated.json()["evidence"]["change_request_number"] == "CR-REF-001"
        attachment = client.put(
            prod
            + f"/projects/{project_id}/activations/purchase-prod-activate/attachments/approval",
            headers={**headers, "content-type": "application/octet-stream"},
            params={"display_name": "Prod evidence"},
            content=b"reference-process-prod-evidence",
        )
        assert attachment.status_code == 201, attachment.text
        listed = client.get(
            prod + f"/projects/{project_id}/activations/purchase-prod-activate/attachments",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        assert listed.json()[0]["attachment_id"] == "approval"

        # Dev activation remains the original published release.
        assert (
            client.get(project_path + "/activation", headers=headers).json()["release_id"]
            == release_id
        )
    assert headers["Authorization"].split(" ")[1] not in log.read_text("utf-8", errors="replace")
