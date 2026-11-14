"""Critical-path same-origin pilot UI mounting without Temporal or live servers."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexusflow.apps import local
from nexusflow.apps.local_ui import (
    PilotUiError,
    attach_pilot_ui,
    require_ui_directory,
    resolve_ui_directory,
)


def _fake_dist(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(
        "<!doctype html><title>pilot</title><body>pilot-ui</body>",
        encoding="utf-8",
    )
    assets = directory / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("window.PILOT=true;", encoding="utf-8")
    (directory / "favicon.ico").write_text("icon", encoding="utf-8")
    return directory


def test_require_ui_directory_fails_closed_when_missing(tmp_path: Path) -> None:
    with pytest.raises(PilotUiError, match="npm run build"):
        require_ui_directory(tmp_path / "missing")


def test_resolve_ui_directory_defaults_to_cwd_web_dist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_ui_directory(None) == (tmp_path / "web" / "dist").resolve()
    explicit = tmp_path / "custom-ui"
    assert resolve_ui_directory(explicit) == explicit.resolve()


def test_attach_pilot_ui_serves_index_assets_and_leaves_api_routes(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path / "web" / "dist")
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/organizations/local-demo/environments/dev/projects")
    def projects() -> dict[str, list[object]]:
        return {"items": []}

    attach_pilot_ui(app, dist)
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/organizations/local-demo/environments/dev/projects").json() == {
        "items": []
    }
    home = client.get("/")
    assert home.status_code == 200
    assert "pilot-ui" in home.text
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "PILOT" in asset.text
    assert client.get("/favicon.ico").text == "icon"
    # In-memory React views have no deep links; unknown paths still return the SPA shell.
    assert "pilot-ui" in client.get("/not-a-file").text


def test_attach_pilot_ui_rejects_path_escape(tmp_path: Path) -> None:
    dist = _fake_dist(tmp_path / "dist")
    secret = tmp_path / "secret.txt"
    secret.write_text("classified", encoding="utf-8")
    app = FastAPI()
    attach_pilot_ui(app, dist)
    client = TestClient(app)
    response = client.get("/../secret.txt")
    assert response.status_code == 200
    assert "classified" not in response.text
    assert "pilot-ui" in response.text


def test_cli_ui_dir_requires_ui_flag(tmp_path: Path, capsys) -> None:
    assert local.main(["serve", "--ui-dir", str(tmp_path), "--state-dir", str(tmp_path)]) == 2
    assert "Pass --ui when using --ui-dir" in capsys.readouterr().err


def test_cli_ui_fails_before_serve_when_build_missing(tmp_path: Path, capsys) -> None:
    assert local.main(["init", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert (
        local.main(
            [
                "serve",
                "--ui",
                "--ui-dir",
                str(tmp_path / "missing-dist"),
                "--state-dir",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "npm run build" in capsys.readouterr().err
