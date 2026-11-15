"""Controlled TLS acceptance for secret-aware authorized HTTP attempts."""

import asyncio
import contextlib

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.authorized_execution import AuthorizedHTTPAttemptExecutor
from nexusflow.modules.connectors.destination import HTTPDestinationPlanner
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.execution_types import HTTPExecutionError
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.secret_types import (
    HTTPAuthorizationCredential,
    SecretResolverError,
)
from nexusflow.modules.connectors.transport import PinnedHTTPTransport
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.tests.connectors import test_transport_acceptance as tls
from nexusflow.tests.connectors.test_definitions import definition

certificates = tls.certificates
SCOPE = Scope(organization_id="organization", environment_id="dev")
TOKEN = "local-demo-bearer-token"
REFERENCE = "opaque-credential-reference"
SUCCESS = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"


class Authorization:
    def authorize(self, scope, permission, resource_id):
        assert scope == SCOPE and permission == "worker.execute" and resource_id == "external-api"
        return "worker-1"


class DNS:
    async def resolve_ipv4(self, host):
        assert host == "api.example.com"
        return ("8.8.8.8", "9.9.9.9")


class Secrets:
    def __init__(self, *, missing=False):
        self.missing = missing
        self.calls = 0

    def resolve(self, scope, secret_reference):
        self.calls += 1
        assert scope == SCOPE and secret_reference == REFERENCE
        if self.missing:
            raise SecretResolverError("credentials_required")
        return HTTPAuthorizationCredential("Bearer", TOKEN)


@contextlib.contextmanager
def setup(tmp_path, *, port, secrets):
    repository = SQLiteConnectionRepository(tmp_path / "connections.db")
    origin = "https://api.example.com" + (f":{port}" if port != 443 else "")
    repository.put(
        SCOPE,
        "external-api",
        "v1",
        definition(base_origin=origin, allowed_origins=[origin], secret_reference=REFERENCE),
        "administrator",
    )
    planner = HTTPDestinationPlanner(repository, Authorization(), DNS(), secrets=secrets)
    destination = HTTPDestinationRequest(
        connection_id="external-api",
        connection_revision="v1",
        operation_id="run-1-step-1",
        path="/secure",
    )
    try:
        yield planner, destination
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_authorized_attempt_sends_bearer_over_controlled_tls(
    tmp_path, certificates, monkeypatch
):
    async with tls.endpoint(certificates, SUCCESS) as (port, names, received, _, _):
        secrets = Secrets()
        with setup(tmp_path, port=port, secrets=secrets) as (planner, destination):
            tls.redirect_dial(monkeypatch)
            result = await AuthorizedHTTPAttemptExecutor(
                planner,
                PinnedHTTPTransport(ssl_context=tls.client_context(certificates)),
            ).execute(SCOPE, destination, HTTPAttemptRequest())
            assert result.status_code == 200
            assert result.operation_id == "run-1-step-1"
            assert secrets.calls >= 2
            assert names == ["api.example.com"]
            assert len(received) == 1
            wire = received[0].decode().lower()
            assert "authorization: bearer local-demo-bearer-token" in wire
            assert TOKEN not in repr(result)
            assert REFERENCE not in repr(result)


@pytest.mark.asyncio
async def test_missing_secret_fails_before_tls(tmp_path, certificates, monkeypatch):
    async with tls.endpoint(certificates, SUCCESS) as (port, _, received, _, _):
        secrets = Secrets(missing=True)
        with setup(tmp_path, port=port, secrets=secrets) as (planner, destination):

            async def dial(*_args, **_kwargs):
                raise AssertionError("No socket may be opened")

            monkeypatch.setattr(asyncio, "open_connection", dial)
            with pytest.raises(HTTPExecutionError) as raised:
                await AuthorizedHTTPAttemptExecutor(
                    planner,
                    PinnedHTTPTransport(ssl_context=tls.client_context(certificates)),
                ).execute(SCOPE, destination, HTTPAttemptRequest())
            assert raised.value.code == "credentials_required"
            assert raised.value.delivery_may_have_occurred is False
            assert received == []
            assert TOKEN not in str(raised.value) + repr(raised.value)
            assert REFERENCE not in str(raised.value) + repr(raised.value)
