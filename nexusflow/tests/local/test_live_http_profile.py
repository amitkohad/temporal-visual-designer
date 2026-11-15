"""Explicit live HTTP enablement preserves revoked identity and incomplete recovery state."""

import json
from unittest.mock import Mock

import pytest

from nexusflow.apps.local_bootstrap import TENANT_ID, initialize
from nexusflow.apps.local_live_http import (
    LocalLiveHTTPWorkerAuthorization,
    enable_live_http,
    load_live_http_profile,
)
from nexusflow.apps.local_secrets import LocalSecretStore
from nexusflow.contracts.models import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant, IdentityError

MARKER = "live-http.json"
SECRETS = "http-secrets.json"
GRANT = "local-live-http-worker"
SCOPE = Scope(organization_id="local-demo", environment_id="dev")
PRIVATE = "bearer-token-sentinel"


def files(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def test_disabled_profile_does_not_create_or_modify_state(tmp_path):
    settings = initialize(tmp_path)
    before = files(tmp_path)
    assert load_live_http_profile(tmp_path, settings) is None
    assert files(tmp_path) == before


def test_enable_and_retry_are_scoped_and_preserve_revoked_worker(tmp_path, monkeypatch):
    settings = initialize(tmp_path)
    before = files(tmp_path)
    profile = enable_live_http(tmp_path)
    assert profile.scope == SCOPE and profile.installation_id == settings.installation_id
    assert profile.secrets_file == SECRETS
    assert profile.profile == "local-demo-live-http"
    for name, content in before.items():
        if name != "identity.db":
            assert (tmp_path / name).read_bytes() == content
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        worker = next(grant for grant in identity.grants(SCOPE) if grant.grant_id == GRANT)
        assert worker == Grant(GRANT, SCOPE, TENANT_ID, "workload", GRANT, "worker")
        identity.revoke_grant(SCOPE, GRANT, expected_revision=1)
    finally:
        identity.close()
    before_retry = files(tmp_path)
    unexpected = Mock(side_effect=AssertionError("retry must not create repositories"))
    monkeypatch.setattr("nexusflow.apps.local_live_http.SQLiteIdentityRepository", unexpected)
    assert enable_live_http(tmp_path) == profile
    assert load_live_http_profile(tmp_path, settings) == profile
    assert files(tmp_path) == before_retry
    unexpected.assert_not_called()


def test_orphan_secrets_file_is_never_adopted(tmp_path):
    settings = initialize(tmp_path)
    (tmp_path / SECRETS).write_text(
        json.dumps({"schema_version": "0.1", "secrets": {}}), encoding="utf-8"
    )
    before = files(tmp_path)
    for operation in (
        lambda: load_live_http_profile(tmp_path, settings),
        lambda: enable_live_http(tmp_path),
    ):
        with pytest.raises(ValueError, match="Incomplete"):
            operation()
    assert files(tmp_path) == before


def test_existing_grant_tombstone_is_not_restored(tmp_path):
    initialize(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        identity.add_membership("local-demo", TENANT_ID, "workload", GRANT)
        identity.save_grant(
            Grant(GRANT, SCOPE, TENANT_ID, "workload", GRANT, "worker"),
            expected_revision=0,
        )
        identity.revoke_grant(SCOPE, GRANT, expected_revision=1)
    finally:
        identity.close()
    before = files(tmp_path)
    with pytest.raises(IdentityError):
        enable_live_http(tmp_path)
    assert files(tmp_path) == before


def test_worker_authorization_grants_execute_in_dev_only():
    worker = LocalLiveHTTPWorkerAuthorization(SCOPE)
    assert worker.authorize(SCOPE, "worker.execute", "api") == GRANT
    with pytest.raises(PermissionError):
        worker.authorize(SCOPE, "connection.manage", "api")
    with pytest.raises(ValueError):
        LocalLiveHTTPWorkerAuthorization(
            Scope(organization_id="local-demo", environment_id="prod")
        )


def test_secret_store_put_and_resolve_never_echo_tokens(tmp_path):
    initialize(tmp_path)
    enable_live_http(tmp_path)
    store = LocalSecretStore(tmp_path / SECRETS, SCOPE)
    store.put("my-api-token", scheme="Bearer", token=PRIVATE)
    resolved = store.resolve(SCOPE, "my-api-token")
    assert resolved.token == PRIVATE and resolved.scheme == "Bearer"
    encoded = (tmp_path / SECRETS).read_text(encoding="utf-8")
    assert PRIVATE in encoded
    assert PRIVATE not in repr(resolved)
    with pytest.raises(Exception) as caught:
        store.resolve(SCOPE, "missing-token")
    assert PRIVATE not in str(caught.value) + repr(caught.value)
