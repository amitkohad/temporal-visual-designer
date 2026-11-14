"""Recorded/mocked Microsoft boundary tests; not live-tenant or production acceptance."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nexusflow.apps.api import create_app
from nexusflow.apps.api_backend import ApiBackend
from nexusflow.apps.entra_pilot import (
    EntraPilotSettings,
    apply_entra_pilot_bootstrap,
    browser_auth_from_settings,
    compose_signing_keys,
    disabled_browser_auth,
    load_entra_pilot_settings,
)
from nexusflow.contracts.models import Scope
from nexusflow.modules.identity_environments.entra_adapters import (
    CompositeSigningKeys,
    EmptyGroupResolver,
    MicrosoftGraphGroupResolver,
    MicrosoftJwksSigningKeys,
)
from nexusflow.modules.identity_environments.entra_http import (
    AllowlistedJsonHttpGet,
    EntraHttpError,
)
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.service import IdentityService
from nexusflow.modules.identity_environments.tokens import EntraTokenVerifier
from nexusflow.modules.identity_environments.types import DirectoryBinding, IdentityError

TENANT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OBJECT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SPA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
AUDIENCE = "api://nexus-flow-pilot"


class RecordedHttp:
    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> dict[str, object]:
        self.calls.append((url, headers))
        if url not in self.responses:
            raise EntraHttpError("unexpected URL")
        return self.responses[url]


class LocalKeys:
    def __init__(self) -> None:
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "local-kid"
        self.jwks_doc = {
            "keys": [
                {
                    **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key())),
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                }
            ]
        }

    def jwks(self, directory_tenant_id: str) -> dict[str, object]:
        assert directory_tenant_id == "11111111-1111-4111-8111-111111111111"
        return self.jwks_doc


def _entra_keys() -> tuple[object, dict[str, object], str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = "entra-kid"
    jwks = {
        "keys": [
            {
                **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())),
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
            }
        ]
    }
    return private, jwks, kid


def _token(private: object, kid: str, *, azp: str = SPA) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "tid": TENANT,
            "oid": OBJECT,
            "sub": OBJECT,
            "ver": "2.0",
            "aud": AUDIENCE,
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "azp": azp,
            "scp": "access_as_user",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
        },
        private,
        algorithm="RS256",
        headers={"kid": kid},
    )


def test_microsoft_jwks_fetches_allowlisted_discovery_document():
    private, jwks, kid = _entra_keys()
    http = RecordedHttp({f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys": jwks})
    keys = MicrosoftJwksSigningKeys(http)
    document = keys.jwks(TENANT)
    assert document["keys"][0]["kid"] == kid
    assert http.calls[0][0].endswith(f"/{TENANT}/discovery/v2.0/keys")
    binding = DirectoryBinding(
        "pilot-org",
        TENANT,
        AUDIENCE,
        allowed_user_clients=(SPA,),
    )
    principal = EntraTokenVerifier(binding, keys).verify(_token(private, kid))
    assert principal.object_id == OBJECT


def test_microsoft_jwks_rejects_empty_keys():
    http = RecordedHttp(
        {f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys": {"keys": []}}
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        MicrosoftJwksSigningKeys(http).jwks(TENANT)


def test_graph_group_resolver_uses_recorded_membership():
    group = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

    class Tokens:
        def access_token(self, directory_tenant_id: str) -> str:
            assert directory_tenant_id == TENANT
            return "graph-token"

    http = RecordedHttp(
        {
            f"https://graph.microsoft.com/v1.0/users/{OBJECT}/"
            "transitiveMemberOf/microsoft.graph.group?$select=id": {"value": [{"id": group}]}
        }
    )
    resolved = MicrosoftGraphGroupResolver(Tokens(), http).groups(TENANT, OBJECT)
    assert resolved == frozenset({group})


def test_graph_group_resolver_fails_closed_on_transport_error():
    class Tokens:
        def access_token(self, directory_tenant_id: str) -> str:
            return "graph-token"

    class Failing:
        def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> dict[str, object]:
            raise EntraHttpError("down")

    with pytest.raises(RuntimeError, match="unavailable"):
        MicrosoftGraphGroupResolver(Tokens(), Failing()).groups(TENANT, OBJECT)


def test_allowlisted_http_rejects_unknown_host():
    client = AllowlistedJsonHttpGet(allowed_hosts=frozenset({"login.microsoftonline.com"}))
    with pytest.raises(EntraHttpError):
        client.get_json("https://evil.example/keys")


def test_composite_signing_keys_routes_by_tenant():
    local = LocalKeys()
    private, jwks, kid = _entra_keys()
    http = RecordedHttp({f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys": jwks})
    composite = CompositeSigningKeys(
        {
            "11111111-1111-4111-8111-111111111111": local,
            TENANT: MicrosoftJwksSigningKeys(http),
        }
    )
    assert composite.jwks("11111111-1111-4111-8111-111111111111")["keys"][0]["kid"] == "local-kid"
    assert composite.jwks(TENANT)["keys"][0]["kid"] == kid
    with pytest.raises(ValueError):
        composite.jwks(str(uuid4()))


def test_load_entra_settings_disabled_without_core_fields(monkeypatch):
    monkeypatch.delenv("NEXUSFLOW_ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("NEXUSFLOW_ENTRA_ORGANIZATION_ID", raising=False)
    assert load_entra_pilot_settings(environ={}) is None


def test_load_entra_settings_from_environ():
    settings = load_entra_pilot_settings(
        environ={
            "NEXUSFLOW_ENTRA_ORGANIZATION_ID": "pilot-org",
            "NEXUSFLOW_ENTRA_TENANT_ID": TENANT,
            "NEXUSFLOW_ENTRA_SPA_CLIENT_ID": SPA,
            "NEXUSFLOW_ENTRA_API_AUDIENCE": AUDIENCE,
            "NEXUSFLOW_ENTRA_API_CLIENT_ID": SPA,
            "NEXUSFLOW_ENTRA_AUTHOR_OBJECT_IDS": OBJECT,
        }
    )
    assert settings is not None
    assert settings.organization_id == "pilot-org"
    assert settings.author_object_ids == (OBJECT,)
    public = browser_auth_from_settings(settings).as_json()
    assert public["enabled"] is True
    assert public["spa_client_id"] == SPA
    assert "client_secret" not in json.dumps(public)


def test_apply_entra_bootstrap_idempotent(tmp_path):
    settings = EntraPilotSettings(
        organization_id="pilot-org",
        directory_tenant_id=TENANT,
        spa_client_id=SPA,
        api_audience=AUDIENCE,
        allowed_user_client_id=SPA,
        author_object_ids=(OBJECT,),
    )
    repo = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        apply_entra_pilot_bootstrap(repo, settings)
        apply_entra_pilot_bootstrap(repo, settings)
        binding = repo.binding("pilot-org")
        assert binding is not None
        assert binding.directory_tenant_id == TENANT
        grants = repo.grants(Scope(organization_id="pilot-org", environment_id="dev"))
        assert any(grant.principal_id == OBJECT for grant in grants)
    finally:
        repo.close()


def test_identity_service_accepts_recorded_entra_token(tmp_path):
    private, jwks, kid = _entra_keys()
    http = RecordedHttp({f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys": jwks})
    settings = EntraPilotSettings(
        organization_id="pilot-org",
        directory_tenant_id=TENANT,
        spa_client_id=SPA,
        api_audience=AUDIENCE,
        allowed_user_client_id=SPA,
        author_object_ids=(OBJECT,),
    )
    repo = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        apply_entra_pilot_bootstrap(repo, settings)
        keys = compose_signing_keys(
            local_tenant_id="11111111-1111-4111-8111-111111111111",
            local_keys=LocalKeys(),
            entra=settings,
        )
        # Replace live Microsoft client with recorded HTTP for the smoke.
        assert isinstance(keys, CompositeSigningKeys)
        keys = CompositeSigningKeys(
            {
                "11111111-1111-4111-8111-111111111111": LocalKeys(),
                TENANT: MicrosoftJwksSigningKeys(http),
            }
        )
        service = IdentityService(repo, keys)
        context = service.authorize(
            _token(private, kid),
            Scope(organization_id="pilot-org", environment_id="dev"),
            "project.read",
            request_id="req",
            correlation_id="corr",
        )
        assert context.actor.directory_tenant_id == TENANT
    finally:
        repo.close()


def test_browser_auth_endpoint_disabled_by_default():
    import tempfile
    from pathlib import Path

    from nexusflow.apps.local_bootstrap import LocalCapabilities, LocalConfigurations, LocalSettings
    from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
    from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
    from nexusflow.modules.durable_runtime.starts import SQLiteStartRepository
    from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
    from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository

    class Runtime:
        async def dispatcher(self, placement):
            raise RuntimeError

        async def snapshot(self, placement, execution_id):
            raise RuntimeError

    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        identity = SQLiteIdentityRepository(directory / "identity.db")
        projects = SQLiteProjectRepository(directory / "projects.db")
        catalog = SQLiteCatalogRepository(directory / "catalog.db")
        intake = SQLiteIntakeRepository(directory / "intake.db")
        activations = SQLiteActivationRepository(directory / "activations.db")
        starts = SQLiteStartRepository(directory / "starts.db")
        settings = LocalSettings(installation_id="11111111-1111-4111-8111-111111111111")
        backend = ApiBackend(
            IdentityService(identity, LocalKeys()),
            projects,
            catalog,
            intake,
            activations,
            starts,
            LocalCapabilities(),
            LocalConfigurations(settings),
            Runtime(),
            browser_auth=disabled_browser_auth(),
        )
        from fastapi.testclient import TestClient

        client = TestClient(create_app(backend))
        response = client.get("/api/v1/auth/browser")
        assert response.status_code == 200
        assert response.json() == {"enabled": False}
        identity.close()
        projects.close()
        catalog.close()
        intake.close()
        activations.close()
        starts.close()


def test_browser_auth_endpoint_exposes_spa_fields_without_secrets(tmp_path):
    from fastapi.testclient import TestClient

    from nexusflow.apps.local_bootstrap import LocalCapabilities, LocalConfigurations, LocalSettings
    from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
    from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
    from nexusflow.modules.durable_runtime.starts import SQLiteStartRepository
    from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
    from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository

    class Runtime:
        async def dispatcher(self, placement):
            raise RuntimeError

        async def snapshot(self, placement, execution_id):
            raise RuntimeError

    settings = EntraPilotSettings(
        organization_id="pilot-org",
        directory_tenant_id=TENANT,
        spa_client_id=SPA,
        api_audience=AUDIENCE,
        allowed_user_client_id=SPA,
        graph_client_secret="super-secret-value",
        graph_client_id=SPA,
    )
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    projects = SQLiteProjectRepository(tmp_path / "projects.db")
    catalog = SQLiteCatalogRepository(tmp_path / "catalog.db")
    intake = SQLiteIntakeRepository(tmp_path / "intake.db")
    activations = SQLiteActivationRepository(tmp_path / "activations.db")
    starts = SQLiteStartRepository(tmp_path / "starts.db")
    local = LocalSettings(installation_id="11111111-1111-4111-8111-111111111111")
    backend = ApiBackend(
        IdentityService(identity, LocalKeys()),
        projects,
        catalog,
        intake,
        activations,
        starts,
        LocalCapabilities(),
        LocalConfigurations(local),
        Runtime(),
        browser_auth=browser_auth_from_settings(settings),
    )
    client = TestClient(create_app(backend))
    payload = client.get("/api/v1/auth/browser").json()
    assert payload["enabled"] is True
    assert payload["organization_id"] == "pilot-org"
    assert "super-secret-value" not in json.dumps(payload)
    assert "graph" not in json.dumps(payload).lower()
    identity.close()
    projects.close()
    catalog.close()
    intake.close()
    activations.close()
    starts.close()


def test_empty_group_resolver():
    assert EmptyGroupResolver().groups(TENANT, OBJECT) == frozenset()


def test_wrong_audience_still_unauthenticated():
    private, jwks, kid = _entra_keys()
    http = RecordedHttp({f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys": jwks})
    binding = DirectoryBinding("pilot-org", TENANT, AUDIENCE, allowed_user_clients=(SPA,))
    with pytest.raises(IdentityError):
        EntraTokenVerifier(binding, MicrosoftJwksSigningKeys(http)).verify(
            _token(private, kid).replace(AUDIENCE, "api://other")
            if False
            else jwt.encode(
                {
                    "tid": TENANT,
                    "oid": OBJECT,
                    "sub": OBJECT,
                    "ver": "2.0",
                    "aud": "api://other",
                    "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                    "azp": SPA,
                    "scp": "access_as_user",
                    "iat": int(time.time()),
                    "nbf": int(time.time()),
                    "exp": int(time.time()) + 300,
                },
                private,
                algorithm="RS256",
                headers={"kid": kid},
            )
        )
