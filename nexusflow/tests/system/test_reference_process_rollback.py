"""SC-04 critical path: activation rollback preserves pinned old runs for reference starters."""

import time

import pytest

from nexusflow.tests.system.helpers import (
    STARTERS,
    _cli,
    _initialized,
    _ports,
    _serve,
    activate_release,
    bump_draft_revision,
    create_publish_activate,
    env_base,
    finish_remaining_approvals,
    pending_task,
    publish_release,
    start_run,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("template", sorted(STARTERS))
def test_reference_starter_rollback_preserves_in_flight_run(tmp_path, template):
    executable = _cli()
    state = tmp_path / "state"
    organization, headers = _initialized(state)
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    starter = STARTERS[template]
    project_id = f"{starter['project_id']}-rollback"
    with _serve(state, executable, api_port, temporal_port, log) as client:
        project_path, workflow, release_a = create_publish_activate(
            client,
            organization=organization,
            template=template,
            headers=headers,
            project_id=project_id,
        )
        base = env_base(organization, "dev")
        old_start = start_run(
            client,
            project_path,
            headers,
            workflow,
            starter["inputs"],
            f"{template}-old-run",
        )
        assert (
            client.get(project_path + "/executions/" + old_start, headers=headers).json()[
                "release_id"
            ]
            == release_a
        )
        first = pending_task(client, base, headers, old_start)
        assert first["request"]["node_invocation_id"] == starter["steps"][0]

        draft = bump_draft_revision(
            client,
            project_path,
            headers,
            expected_revision=1,
            summary="Release B for rollback breadth",
        )
        assert draft == 2
        release_b = publish_release(
            client,
            project_path,
            headers,
            source_revision=2,
            idempotency_key=f"{project_id}-release-b",
        )
        assert release_b != release_a
        activated_b = activate_release(
            client,
            project_path,
            headers,
            project_id=project_id,
            release_id=release_b,
            operation_id=f"{project_id}-activate-b",
            expected_revision=1,
        )
        assert activated_b["release_id"] == release_b
        assert activated_b["revision"] == 2
        assert (
            client.get(project_path + "/executions/" + old_start, headers=headers).json()[
                "release_id"
            ]
            == release_a
        )

        rollback_op = f"{project_id}-rollback-a"
        rolled = activate_release(
            client,
            project_path,
            headers,
            project_id=project_id,
            release_id=release_a,
            operation_id=rollback_op,
            expected_revision=2,
            action="rollback",
        )
        assert rolled["release_id"] == release_a
        assert rolled["previous_release_id"] == release_b
        assert rolled["operation_id"] == rollback_op
        receipt = client.get(project_path + "/activations/" + rollback_op, headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["action"] == "rollback"
        assert (
            client.get(project_path + "/activation", headers=headers).json()["release_id"]
            == release_a
        )

        # In-flight old run stays pinned to release A and still completes after rollback.
        old_status = client.get(project_path + "/executions/" + old_start, headers=headers)
        assert old_status.status_code == 200, old_status.text
        assert old_status.json()["release_id"] == release_a
        assert old_status.json()["state"] != "completed"
        finished = finish_remaining_approvals(
            client,
            organization=organization,
            template=template,
            headers=headers,
            project_path=project_path,
            execution=old_start,
            remaining_steps=starter["steps"],
        )
        assert finished["release_id"] == release_a

        # Future starts after rollback resolve the restored release (no second approval cycle).
        payload = {
            "workflow_id": workflow,
            "idempotency_key": f"{template}-after-rollback",
            "input": starter["inputs"],
        }
        deadline = time.monotonic() + 20
        after = None
        while time.monotonic() < deadline:
            after = client.post(project_path + "/executions", headers=headers, json=payload)
            if after.status_code == 202:
                break
            if after.status_code != 503:
                break
            time.sleep(0.2)
        assert after is not None and after.status_code == 202, getattr(after, "text", after)
        assert after.json()["release_id"] == release_a
    assert headers["Authorization"].split(" ")[1] not in log.read_text("utf-8", errors="replace")
