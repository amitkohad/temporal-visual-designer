"""Live Microsoft JWKS/Graph adapters for Feature 01; no fake identity in deployment.

These adapters talk only to allowlisted Microsoft hosts through an injected HTTP
port. Default tests inject recorded responses and never claim production
acceptance from those fixtures. Live-tenant smoke remains a separate gate.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID

from nexusflow.modules.identity_environments.entra_http import (
    AllowlistedJsonHttpGet,
    EntraHttpError,
    JsonHttpGet,
)
from nexusflow.modules.identity_environments.types import GroupResolver, SigningKeys

MICROSOFT_LOGIN_HOST = "login.microsoftonline.com"
MICROSOFT_GRAPH_HOST = "graph.microsoft.com"


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value.lower():
        raise ValueError("Invalid identifier")
    return value.lower()


def _nonempty(value: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Invalid credential field")
    return value.strip()


class MicrosoftJwksSigningKeys:
    """Fetch Entra v2 signing keys from the tenant's discovery document."""

    def __init__(
        self,
        http: JsonHttpGet | None = None,
        *,
        host: str = MICROSOFT_LOGIN_HOST,
    ) -> None:
        if host != MICROSOFT_LOGIN_HOST:
            raise ValueError("Only the Microsoft login host is supported")
        self.host = host
        self.http = http or AllowlistedJsonHttpGet(allowed_hosts=frozenset({host}))

    def jwks(self, directory_tenant_id: str) -> dict[str, object]:
        tenant = _uuid(directory_tenant_id)
        url = f"https://{self.host}/{tenant}/discovery/v2.0/keys"
        try:
            document = self.http.get_json(url)
        except EntraHttpError as error:
            raise RuntimeError("Identity provider unavailable") from error
        keys = document.get("keys")
        if not isinstance(keys, list) or not keys:
            raise RuntimeError("Identity provider unavailable")
        return {"keys": keys}


class EmptyGroupResolver:
    """Local-demo / OID-grant path: no directory groups."""

    def groups(self, directory_tenant_id: str, object_id: str) -> frozenset[str]:
        _uuid(directory_tenant_id)
        _uuid(object_id)
        return frozenset()


class GraphTokenProvider(Protocol):
    def access_token(self, directory_tenant_id: str) -> str: ...


class MicrosoftGraphGroupResolver:
    """Resolve current transitive group membership via Microsoft Graph.

    Requires an app-only access token supplier. Failures raise and never fall
    back to signed stale groups (ADR 0003).
    """

    def __init__(
        self,
        token_provider: GraphTokenProvider,
        http: JsonHttpGet | None = None,
        *,
        host: str = MICROSOFT_GRAPH_HOST,
        maximum_groups: int = 2048,
    ) -> None:
        if host != MICROSOFT_GRAPH_HOST:
            raise ValueError("Only the Microsoft Graph host is supported")
        if type(maximum_groups) is not int or not 1 <= maximum_groups <= 2048:
            raise ValueError("Invalid group limit")
        self.host = host
        self.token_provider = token_provider
        self.http = http or AllowlistedJsonHttpGet(allowed_hosts=frozenset({host}))
        self.maximum_groups = maximum_groups

    def groups(self, directory_tenant_id: str, object_id: str) -> frozenset[str]:
        _uuid(directory_tenant_id)
        oid = _uuid(object_id)
        try:
            token = self.token_provider.access_token(directory_tenant_id)
        except Exception as error:
            raise RuntimeError("Directory membership unavailable") from error
        if not isinstance(token, str) or not token or len(token) > 16_384:
            raise RuntimeError("Directory membership unavailable")
        url = (
            f"https://{self.host}/v1.0/users/{oid}/"
            "transitiveMemberOf/microsoft.graph.group?$select=id"
        )
        try:
            payload = self.http.get_json(url, headers={"Authorization": f"Bearer {token}"})
        except EntraHttpError as error:
            raise RuntimeError("Directory membership unavailable") from error
        values = payload.get("value")
        if not isinstance(values, list) or len(values) > self.maximum_groups:
            raise RuntimeError("Directory membership unavailable")
        if payload.get("@odata.nextLink"):
            raise RuntimeError("Directory membership unavailable")
        group_ids: set[str] = set()
        for item in values:
            if not isinstance(item, dict):
                raise RuntimeError("Directory membership unavailable")
            group_ids.add(_uuid(item.get("id")))
        return frozenset(group_ids)


class CompositeSigningKeys:
    """Route JWKS lookup by directory tenant (local-demo + Entra coexist)."""

    def __init__(self, providers: Mapping[str, SigningKeys]) -> None:
        if not providers:
            raise ValueError("At least one signing-key provider is required")
        mapping: dict[str, SigningKeys] = {}
        for tenant, provider in providers.items():
            mapping[_uuid(tenant)] = provider
        self._providers = mapping

    def jwks(self, directory_tenant_id: str) -> dict[str, object]:
        provider = self._providers.get(_uuid(directory_tenant_id))
        if provider is None:
            raise ValueError("Unknown directory")
        return provider.jwks(directory_tenant_id)


class CompositeGroupResolver:
    """Route group resolution by directory tenant."""

    def __init__(self, providers: Mapping[str, GroupResolver]) -> None:
        if not providers:
            raise ValueError("At least one group resolver is required")
        mapping: dict[str, GroupResolver] = {}
        for tenant, provider in providers.items():
            mapping[_uuid(tenant)] = provider
        self._providers = mapping

    def groups(self, directory_tenant_id: str, object_id: str) -> frozenset[str]:
        provider = self._providers.get(_uuid(directory_tenant_id))
        if provider is None:
            raise RuntimeError("Directory membership unavailable")
        return provider.groups(directory_tenant_id, object_id)


class ClientCredentialsTokenProvider:
    """App-only token for Graph using the v2.0 client_credentials grant."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        host: str = MICROSOFT_LOGIN_HOST,
        scope: str = "https://graph.microsoft.com/.default",
        timeout_seconds: float = 5,
    ) -> None:
        if host != MICROSOFT_LOGIN_HOST:
            raise ValueError("Only the Microsoft login host is supported")
        self.client_id = _nonempty(client_id)
        self.client_secret = _nonempty(client_secret, maximum=4096)
        self.scope = _nonempty(scope, maximum=512)
        self.host = host
        self.timeout_seconds = float(timeout_seconds)

    def access_token(self, directory_tenant_id: str) -> str:
        tenant = _uuid(directory_tenant_id)
        body = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
                "scope": self.scope,
            }
        ).encode("utf-8")
        url = f"https://{self.host}/{tenant}/oauth2/v2.0/token"
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if getattr(response, "status", None) != 200:
                    raise RuntimeError("Directory membership unavailable")
                payload = json.loads(response.read(65536).decode("utf-8"))
        except Exception as error:
            raise RuntimeError("Directory membership unavailable") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Directory membership unavailable")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Directory membership unavailable")
        return token
