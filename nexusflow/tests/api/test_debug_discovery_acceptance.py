"""Independent signed HTTP acceptance of owner-scoped metadata discovery."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts import Scope
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_session_acceptance import OTHER, debug, start  # noqa: F401

PATH = BASE + "/projects/hr/debug-sessions"


def test_discovery_pages_metadata_without_runtime_or_current_source(debug, monkeypatch):  # noqa: F811
    client, backend, _, runtime = debug
    sessions = []
    for number in range(3):
        result = start(client, operation_id=f"discover-{number}")
        assert result.status_code == 202, result.text
        sessions.append(result.json())
    calls = list(runtime.calls)
    runtime.fail = True
    backend.configurations.available = False
    monkeypatch.setattr(backend.projects, "revision", Mock(side_effect=OSError("private-source")))
    first = client.get(PATH, params={"limit": 1})
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    collected = first.json()["items"]
    cursor = first.json()["next_cursor"]
    while cursor:
        page = client.get(PATH, params={"limit": 1, "cursor": cursor})
        assert page.status_code == 200, page.text
        collected.extend(page.json()["items"])
        cursor = page.json()["next_cursor"]
    expected = sorted(
        sessions, key=lambda row: (row["created_at"], row["session_id"]), reverse=True
    )
    assert collected == expected
    assert runtime.calls == calls
    assert "never-in-metadata" not in first.text
    assert all(set(item) == set(sessions[0]) for item in collected)
    assert client.get(BASE + "/projects/absent/debug-sessions").json() == {
        "items": [],
        "next_cursor": None,
    }


def test_discovery_enforces_owner_context_and_fresh_revocation(debug):  # noqa: F811
    client, _, data, runtime = debug
    assert start(client, operation_id="first").status_code == 202
    assert start(client, operation_id="second").status_code == 202
    first = client.get(PATH, params={"limit": 1})
    assert first.status_code == 200, first.text
    owner_cursor = first.json()["next_cursor"]
    assert owner_cursor
    identities, scope, grant, _, token = data
    identities.add_membership("acme", grant.directory_tenant_id, "user", OTHER)
    identities.save_grant(
        replace(grant, grant_id="other-discovery", principal_id=OTHER), expected_revision=0
    )
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-discovery-subject")
    foreign = client.get(
        PATH,
        params={
            "actor_id": first.json()["items"][0]["actor_id"],
            "owner": first.json()["items"][0]["actor_id"],
        },
    )
    assert foreign.status_code == 200 and foreign.json()["items"] == []
    assert client.get(PATH, params={"cursor": owner_cursor}).status_code == 422
    owned = start(client, operation_id="other-owned")
    assert owned.status_code == 202, owned.text
    assert client.get(PATH).json()["items"] == [owned.json()]
    client.headers["authorization"] = "Bearer " + token()
    calls = list(runtime.calls)
    identities.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(PATH).status_code == 403
    assert client.get(PATH, params={"cursor": "invalid!"}).status_code == 403
    assert runtime.calls == calls


def test_pending_discovery_preserves_order_after_acknowledgement(debug):  # noqa: F811
    client, backend, data, runtime = debug
    assert start(client, operation_id="older").status_code == 202
    runtime.fail = True
    assert start(client, operation_id="pending").status_code == 503
    before = client.get(PATH, params={"limit": 1})
    assert before.status_code == 200, before.text
    assert before.json()["items"][0]["state"] == "dispatch_pending"
    cursor = before.json()["next_cursor"]
    assert cursor
    runtime.fail = False
    acknowledged = start(client, operation_id="pending")
    assert acknowledged.status_code == 202, acknowledged.text
    after = client.get(PATH, params={"limit": 1})
    assert after.json()["next_cursor"] == cursor
    assert after.json()["items"] == [acknowledged.json()]
    assert len(client.get(PATH, params={"cursor": cursor}).json()["items"]) == 1
    assert backend.activations.current(data[1], "hr") is None


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_nondevelopment_discovery_denied_before_unconfigured_provider(debug, environment):  # noqa: F811
    client, backend, data, _ = debug
    identities, _, grant, _, _ = data
    identities.save_grant(
        replace(grant, scope=Scope(organization_id="acme", environment_id=environment)),
        expected_revision=0,
    )
    with TestClient(
        create_app(replace(backend, debug_sessions=None, debug_runtime=None)),
        raise_server_exceptions=False,
    ) as other:
        other.headers.update(client.headers)
        assert other.get(PATH.replace("/dev", "/" + environment)).status_code == 403


def test_corrupt_or_closed_metadata_provider_returns_safe_failure(debug):  # noqa: F811
    client, backend, _, _ = debug
    assert start(client).status_code == 202
    with backend.debug_sessions.connection:
        backend.debug_sessions.connection.execute(
            "UPDATE debug_sessions SET session=?", ("private-corrupt-metadata",)
        )
    failed = client.get(PATH)
    assert failed.status_code == 503, failed.text
    assert "private-corrupt-metadata" not in failed.text
    backend.debug_sessions.close()
    assert client.get(PATH).status_code == 503
