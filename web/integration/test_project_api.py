"""Explicit Chromium -> Vite proxy -> signed FastAPI -> real SQLite acceptance."""

import copy
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from nexusflow.apps.api import create_app
from nexusflow.modules.project_versioning.canonical import digest, executable
from nexusflow.modules.project_versioning.types import ProjectContent
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


def _ready(url, process=None):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            pytest.fail("Browser development server exited before readiness")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.05)
    pytest.fail("Local browser acceptance server did not become ready")


@pytest.mark.integration
def test_browser_saves_layout_without_rewriting_executable_project(api):  # noqa: F811
    client, backend, _, runtime = api
    node = shutil.which("node")
    assert node is not None, "Install Node and web dependencies for this explicit suite"
    payload = json.loads((ROOT / "examples/local-approval-project.json").read_text())
    payload["project_id"] = "browser-project"
    second = copy.deepcopy(payload["content"]["workflows"][0])
    second["workflow_id"] = "second-workflow"
    payload["content"]["workflows"].append(second)
    payload["content"]["layout"] = {"existing_owner": {"preserve": True}}
    created = client.post(BASE + "/projects", json=payload)
    assert created.status_code == 201, created.text
    original = created.json()["content"]
    token = client.headers["authorization"].removeprefix("Bearer ")

    api_socket = socket.socket()
    api_socket.bind(("127.0.0.1", 0))
    api_port = api_socket.getsockname()[1]
    api_socket.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(create_app(backend), log_level="critical", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, daemon=True)
    thread.start()
    vite = None
    try:
        _ready(f"http://127.0.0.1:{api_port}/health")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            web_port = reservation.getsockname()[1]
        environment = {
            **os.environ,
            "NEXUSFLOW_API_TARGET": f"http://127.0.0.1:{api_port}",
        }
        vite = subprocess.Popen(
            [
                node,
                str(WEB / "node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
                "--strictPort",
            ],
            cwd=WEB,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _ready(f"http://127.0.0.1:{web_port}", vite)
        completed = subprocess.run(
            [node, str(WEB / "integration/real-project.mjs")],
            cwd=WEB,
            env={
                **environment,
                "NEXUSFLOW_E2E_URL": f"http://127.0.0.1:{web_port}",
                "NEXUSFLOW_E2E_TOKEN": token,
            },
            capture_output=True,
            text=True,
            timeout=90,
        )
        diagnostics = (completed.stdout + completed.stderr).replace(token, "[redacted]")
        assert completed.returncode == 0, diagnostics
        latest = client.get(BASE + "/projects/browser-project").json()
        assert latest["revision"] == 3
        saved = latest["content"]
        assert {key: value for key, value in saved.items() if key != "layout"} == {
            key: value for key, value in original.items() if key != "layout"
        }
        assert saved["layout"]["existing_owner"] == {"preserve": True}
        presentation = saved["layout"]["nexus_flow_designer"]["workflows"]["approval-demo"]
        assert presentation["nodes"]["approval"]["label"] == "Finance review"
        assert presentation["nodes"]["approval"]["description"] == "Review the request"
        assert token not in json.dumps(saved)
        assert digest(executable(ProjectContent.model_validate_json(json.dumps(saved)))) == digest(
            executable(ProjectContent.model_validate_json(json.dumps(original)))
        )
        assert runtime.plans == {} and runtime.snapshot_calls == []
    finally:
        if vite is not None:
            vite.terminate()
            vite.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=10)
        api_socket.close()
        assert not thread.is_alive(), "Local API server did not stop"
