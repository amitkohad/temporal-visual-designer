"""Bootstrap uses real repositories, authorization and release readiness."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.apps.local_bootstrap import (
    LocalConfigurations,
    initialize,
    issue_token,
    load_settings,
    open_backend,
)
from nexusflow.contracts.models import Scope

SCOPE = Scope(organization_id="local-demo", environment_id="dev")
BASE = "/api/v1/organizations/local-demo/environments/dev"


class Runtime:
    async def dispatcher(self, placement):
        return self

    async def start(self, execution_id, plan):
        self.plan = plan

    async def snapshot(self, placement, execution_id):
        raise AssertionError("Not used")


def test_real_seed_and_scoped_signed_token(tmp_path):
    initialize(tmp_path)
    runtime = Runtime()
    with open_backend(tmp_path, runtime) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.get(BASE + "/projects").json()[0]["project_id"] == "welcome"
        active = client.get(BASE + "/projects/welcome/activation")
        assert active.status_code == 200 and active.json()["revision"] == 1
        started = client.post(
            BASE + "/projects/welcome/executions",
            json={"workflow_id": "welcome", "idempotency_key": "first"},
        )
        assert started.status_code == 202, started.text
        assert started.json()["release_id"] == active.json()["release_id"]
        assert [s.kind for s in runtime.plan.steps] == ["start", "wait", "end"]
        for env in ("uat", "prod"):
            assert client.get(BASE.replace("/dev", "/" + env) + "/projects").status_code == 403
        assert client.get(BASE.replace("local-demo", "other") + "/projects").status_code == 401


def test_idempotency_preserves_edits_keys_and_revoked_grants(tmp_path):
    settings = initialize(tmp_path)
    key = (tmp_path / "private-key.pem").read_bytes()
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        path = BASE + "/projects/welcome"
        content = client.get(path).json()["content"]
        content["layout"] = {"edited": True}
        assert (
            client.put(
                path, json={"expected_revision": 1, "summary": "Changed", "content": content}
            ).status_code
            == 200
        )
        backend.identity.repository.revoke_grant(SCOPE, "local-author", expected_revision=1)
    assert initialize(tmp_path) == settings
    assert (tmp_path / "private-key.pem").read_bytes() == key
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        assert backend.projects.revision(SCOPE, "welcome").number == 2
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.get(path).status_code == 403


def test_installations_have_isolated_signing_keys(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    initialize(first)
    initialize(second)
    with open_backend(second, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(first)
        assert client.get(BASE + "/projects").status_code == 401


@pytest.mark.parametrize("mutation", ["missing", "jwks", "profile", "revision", "binding"])
def test_incomplete_or_mismatched_state_fails_closed(tmp_path, mutation):
    initialize(tmp_path)
    if mutation == "missing":
        (tmp_path / "projects.db").unlink()
    elif mutation == "jwks":
        (tmp_path / "public-jwks.json").write_text('{"keys": []}')
    elif mutation == "binding":
        with sqlite3.connect(tmp_path / "identity.db") as db:
            db.execute("DELETE FROM bindings")
    else:
        path = tmp_path / "settings.json"
        settings = json.loads(path.read_text())
        settings["profile" if mutation == "profile" else "configuration_revision"] = "invalid"
        path.write_text(json.dumps(settings))
    with pytest.raises(ValueError, match="Invalid or incomplete"):
        load_settings(tmp_path)
    with pytest.raises(ValueError):
        initialize(tmp_path)


def test_unrecognized_populated_directory_is_preserved_and_lock_is_allowed(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("keep")
    with pytest.raises(ValueError, match="populated"):
        initialize(tmp_path)
    assert path.read_text() == "keep"
    path.unlink()
    (tmp_path / ".launcher.lock").touch()
    initialize(tmp_path)


def test_repository_cleanup_on_exception(tmp_path):
    initialize(tmp_path)
    with pytest.raises(RuntimeError), open_backend(tmp_path, Runtime()) as backend:
        raise RuntimeError("stop")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        backend.projects.list_projects(SCOPE)


def test_local_configuration_validates_actual_empty_object_schema(tmp_path):
    config = LocalConfigurations(initialize(tmp_path))
    assert config.supports(SCOPE, "welcome", "local-v1", {"type": "object"})
    assert not config.supports(SCOPE, "welcome", "changed", {})
    assert not config.supports(
        SCOPE.model_copy(update={"environment_id": "prod"}), "welcome", "local-v1", {}
    )
    assert not config.supports(SCOPE, "welcome", "local-v1", {"required": ["secret"]})
    assert not config.supports(SCOPE, "welcome", "local-v1", {"$ref": "https://example.com/schema"})


@pytest.mark.parametrize("lifetime", [0, -1, 86401, True, 1.5])
def test_invalid_token_lifetimes(tmp_path, lifetime):
    with pytest.raises(ValueError):
        issue_token(tmp_path, lifetime)


def test_interrupted_initialization_has_no_success_marker_and_cannot_reseed(tmp_path, monkeypatch):
    from nexusflow.modules.project_versioning.service import ProjectService

    def unavailable(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(ProjectService, "publish", unavailable)
    with pytest.raises(OSError, match="disk failure"):
        initialize(tmp_path)
    assert not (tmp_path / "settings.json").exists()
    key = (tmp_path / "private-key.pem").read_bytes()
    with pytest.raises(ValueError, match="incomplete"):
        initialize(tmp_path)
    assert (tmp_path / "private-key.pem").read_bytes() == key


def test_empty_database_cannot_be_silently_recreated(tmp_path):
    initialize(tmp_path)
    (tmp_path / "starts.db").write_bytes(b"")
    with pytest.raises(ValueError, match="Invalid or incomplete"):
        load_settings(tmp_path)
    assert (tmp_path / "starts.db").read_bytes() == b""
