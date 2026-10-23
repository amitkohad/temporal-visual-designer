"""Fail-closed Entra v2 verification using trusted, injected directory providers.

This module performs no HTTP requests and never follows token-supplied key or
claim-source URLs. Production JWKS/Graph adapters are deliberately outside this proof.
"""

from uuid import UUID

import jwt

from nexusflow.contracts.models import Actor
from nexusflow.modules.identity_environments.types import (
    DirectoryBinding,
    GroupResolver,
    IdentityError,
    Principal,
    SigningKeys,
)

_MAX_TOKEN = 32_768
_MAX_GROUPS = 2048


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value.lower():
        raise ValueError("Invalid identifier")
    return value.lower()


class EntraTokenVerifier:
    def __init__(
        self, binding: DirectoryBinding, keys: SigningKeys, groups: GroupResolver | None = None
    ) -> None:
        self.binding = binding
        self.keys = keys
        self.groups = groups

    def verify(self, token: str) -> Principal:
        try:
            return self._verify(token)
        except IdentityError:
            raise
        except Exception:
            raise IdentityError("unauthenticated", "Invalid access token") from None

    def _verify(self, token: str) -> Principal:
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN:
            raise ValueError("Invalid token")
        tenant = _uuid(self.binding.directory_tenant_id)
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if (
            header.get("alg") != "RS256"
            or not isinstance(kid, str)
            or not 1 <= len(kid) <= 256
            or any(name in header for name in ("jku", "jwk", "x5u", "crit"))
        ):
            raise ValueError("Invalid header")
        try:
            document = self.keys.jwks(tenant)
        except Exception:
            raise IdentityError("unavailable", "Identity provider unavailable") from None
        candidates = document.get("keys")
        if not isinstance(candidates, list):
            raise ValueError("Invalid signing keys")
        matching = [key for key in candidates if isinstance(key, dict) and key.get("kid") == kid]
        if len(matching) != 1:
            raise ValueError("Unknown signing key")
        key = matching[0]
        if (
            key.get("kty") != "RSA"
            or key.get("alg", "RS256") != "RS256"
            or key.get("use", "sig") != "sig"
            or ("key_ops" in key and "verify" not in key["key_ops"])
        ):
            raise ValueError("Invalid signing key")
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key, algorithm="RS256").key,
            algorithms=["RS256"],
            audience=self.binding.audience,
            issuer=issuer,
            options={"require": ["tid", "sub", "oid", "exp", "iat", "nbf", "ver", "azp"]},
        )
        if _uuid(claims["tid"]) != tenant or claims["ver"] != "2.0":
            raise ValueError("Invalid tenant or version")
        oid = _uuid(claims["oid"])
        if not isinstance(claims["sub"], str) or not claims["sub"].strip():
            raise ValueError("Invalid subject")
        for field in ("exp", "iat", "nbf"):
            if type(claims[field]) is not int:
                raise ValueError("Invalid timestamp")
        client = claims["azp"]
        if claims.get("idtyp") == "app":
            roles = claims.get("roles")
            if (
                client not in self.binding.allowed_workload_clients
                or not isinstance(roles, list)
                or "NexusFlow.Worker" not in roles
                or "scp" in claims
            ):
                raise ValueError("Invalid workload")
            return Principal(
                Actor(issuer=issuer, subject=claims["sub"], directory_tenant_id=tenant),
                oid,
                "workload",
            )
        if claims.get("idtyp") not in (None, "user"):
            raise ValueError("Invalid principal type")
        scopes = claims.get("scp")
        if (
            client not in self.binding.allowed_user_clients
            or not isinstance(scopes, str)
            or self.binding.delegated_scope not in scopes.split()
        ):
            raise ValueError("Invalid delegation")
        overage = claims.get("_claim_names", {})
        if not isinstance(overage, dict):
            raise ValueError("Invalid group claims")
        signed_groups = claims.get("groups", [])
        if not isinstance(signed_groups, list) or len(signed_groups) > _MAX_GROUPS:
            raise ValueError("Invalid groups")
        for value in signed_groups:
            _uuid(value)
        if self.groups is not None:
            try:
                memberships = self.groups.groups(tenant, oid)
            except Exception:
                raise IdentityError("unavailable", "Directory membership unavailable") from None
        else:
            if signed_groups or "groups" in overage or "hasgroups" in claims:
                raise IdentityError("unavailable", "Directory membership unavailable")
            memberships = frozenset()
        if not isinstance(memberships, (list, frozenset, tuple)) or len(memberships) > _MAX_GROUPS:
            raise ValueError("Invalid groups")
        group_ids = frozenset(_uuid(value) for value in memberships)
        return Principal(
            Actor(issuer=issuer, subject=claims["sub"], directory_tenant_id=tenant),
            oid,
            "user",
            group_ids,
        )
