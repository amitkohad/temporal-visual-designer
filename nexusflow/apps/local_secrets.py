"""File-backed local-demo secret map; never a production secret provider."""

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.secret_types import (
    HTTPAuthorizationCredential,
    HTTPAuthorizationScheme,
    SecretResolverError,
)

_SCHEMA_VERSION: Literal["0.1"] = "0.1"
_MAX_BYTES = 1048576


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError


class LocalSecretStore:
    """Dev-only JSON map under the installation directory; tokens never enter logs."""

    def __init__(self, path: Path, scope: Scope) -> None:
        if type(scope) is not Scope or scope.environment_id != "dev":
            raise ValueError("Local secret store supports only Dev")
        if path.is_symlink():
            raise ValueError("Invalid local secret path")
        self.path = path
        self.scope = Scope.model_validate(
            {"organization_id": scope.organization_id, "environment_id": scope.environment_id},
            strict=True,
        )

    def load(self) -> dict[str, HTTPAuthorizationCredential]:
        """Read the bounded secret map; missing file is empty only for create helpers."""
        if self.path.is_symlink():
            raise SecretResolverError("unavailable") from None
        if not self.path.exists():
            raise SecretResolverError("credentials_required") from None
        try:
            with self.path.open("rb") as stream:
                encoded = stream.read(_MAX_BYTES + 1)
            if len(encoded) > _MAX_BYTES:
                raise ValueError
            document = json.loads(encoded, object_pairs_hook=_unique, parse_constant=_constant)
            if (
                type(document) is not dict
                or document.keys() != {"schema_version", "secrets"}
                or document["schema_version"] != _SCHEMA_VERSION
                or type(document["secrets"]) is not dict
                or len(document["secrets"]) > 1000
            ):
                raise ValueError
            result: dict[str, HTTPAuthorizationCredential] = {}
            for reference, entry in document["secrets"].items():
                TypeAdapter(Identifier).validate_python(reference, strict=True)
                if (
                    type(entry) is not dict
                    or entry.keys() != {"scheme", "token"}
                    or entry["scheme"] not in {"Bearer"}
                ):
                    raise ValueError
                result[reference] = HTTPAuthorizationCredential(entry["scheme"], entry["token"])
            return result
        except SecretResolverError:
            raise
        except Exception:
            raise SecretResolverError("unavailable") from None

    def put(self, reference: str, *, scheme: HTTPAuthorizationScheme, token: str) -> None:
        """Create or replace one opaque reference; never logs token material."""
        try:
            selected = TypeAdapter(Identifier).validate_python(reference, strict=True)
            credential = HTTPAuthorizationCredential(scheme, token)
        except Exception:
            raise SecretResolverError("invalid_request") from None
        try:
            existing: dict[str, HTTPAuthorizationCredential]
            if self.path.exists() and not self.path.is_symlink():
                existing = self.load()
            else:
                if self.path.is_symlink():
                    raise SecretResolverError("unavailable") from None
                existing = {}
            existing[selected] = credential
            payload = {
                "schema_version": _SCHEMA_VERSION,
                "secrets": {
                    key: {"scheme": value.scheme, "token": value.token}
                    for key, value in sorted(existing.items())
                },
            }
            encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            if len(encoded.encode("utf-8")) > _MAX_BYTES:
                raise SecretResolverError("invalid_request") from None
            temporary = self.path.with_name(self.path.name + ".tmp")
            if temporary.is_symlink() or temporary.exists():
                temporary.unlink()
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except SecretResolverError:
            raise
        except Exception:
            raise SecretResolverError("unavailable") from None

    def resolve(self, scope: Scope, secret_reference: Identifier) -> HTTPAuthorizationCredential:
        try:
            selected_scope = Scope.model_validate(
                {
                    "organization_id": scope.organization_id,
                    "environment_id": scope.environment_id,
                },
                strict=True,
            )
            reference = TypeAdapter(Identifier).validate_python(secret_reference, strict=True)
        except Exception:
            raise SecretResolverError("invalid_request") from None
        if selected_scope != self.scope or selected_scope.environment_id != "dev":
            raise SecretResolverError("forbidden") from None
        secrets = self.load()
        credential = secrets.get(reference)
        if credential is None:
            raise SecretResolverError("credentials_required") from None
        return HTTPAuthorizationCredential(credential.scheme, credential.token)


def ensure_secret_map(path: Path) -> None:
    """Create an empty secret map when absent; never overwrite existing values."""
    if path.is_symlink():
        raise ValueError("Invalid local secret path")
    if path.exists():
        try:
            with path.open("rb") as stream:
                encoded = stream.read(_MAX_BYTES + 1)
            if len(encoded) > _MAX_BYTES:
                raise ValueError
            document = json.loads(encoded, object_pairs_hook=_unique, parse_constant=_constant)
            if (
                type(document) is not dict
                or document.keys() != {"schema_version", "secrets"}
                or document["schema_version"] != _SCHEMA_VERSION
                or type(document["secrets"]) is not dict
            ):
                raise ValueError
        except Exception:
            raise ValueError("Invalid local secret map; preserve existing state") from None
        return
    payload = {"schema_version": _SCHEMA_VERSION, "secrets": {}}
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
