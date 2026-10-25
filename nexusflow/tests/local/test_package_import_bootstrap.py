"""Package intake storage is additive, persistent and owned by local composition."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.apps.local_bootstrap import (
    FILES,
    initialize,
    issue_token,
    load_settings,
    open_backend,
)
from nexusflow.contracts.models import Scope
from nexusflow.tests.local.test_bootstrap import BASE, Runtime


def test_additive_provider_persists_without_reinitialization(tmp_path):
    settings = initialize(tmp_path)
    database = tmp_path / "package-imports.db"
    assert "package-imports.db" not in FILES
    database.unlink()
    assert load_settings(tmp_path) == settings
    key = (tmp_path / "private-key.pem").read_bytes()
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        repository = backend.package_imports
        assert repository is not None and database.exists()
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        release = client.get(BASE + "/projects/welcome/activation").json()["release_id"]
        archive = client.get(BASE + "/projects/welcome/releases/" + release + "/package")
        assert archive.status_code == 200, archive.text
        result = client.put(
            BASE + "/package-imports/demo",
            content=archive.content,
            headers={"content-type": "application/zip"},
        )
        assert result.status_code == 201, result.text
        metadata = result.json()
    with pytest.raises(sqlite3.ProgrammingError):
        repository.get(Scope(organization_id="local-demo", environment_id="dev"), "demo")
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.get(BASE + "/package-imports/demo").json() == metadata
        assert client.get(BASE + "/package-imports/demo/archive").content == archive.content
    assert (tmp_path / "private-key.pem").read_bytes() == key
    assert load_settings(tmp_path) == settings
