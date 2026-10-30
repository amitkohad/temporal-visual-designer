"""Explicit trusted Dev scope configuration; never infer eligibility from a name."""

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.destination_types import WorkerDestinationAuthorization
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockGrant


class ExplicitDevHTTPMockPolicy:
    def __init__(
        self, worker_authorization: WorkerDestinationAuthorization, dev_scopes: tuple[Scope, ...]
    ) -> None:
        try:
            if type(dev_scopes) is not tuple or len(dev_scopes) > 1000:
                raise ValueError
            keys = []
            for scope in dev_scopes:
                selected = self._scope(scope)
                keys.append((selected.organization_id, selected.environment_id))
            if len(set(keys)) != len(keys):
                raise ValueError
        except Exception:
            raise ValueError("Invalid HTTP mock Dev scope configuration") from None
        self._dev_scopes = frozenset(keys)
        self._worker = worker_authorization

    @staticmethod
    def _scope(scope: Scope) -> Scope:
        if type(scope) is not Scope:
            raise HTTPMockError("invalid_request") from None
        try:
            return Scope.model_validate(
                {"organization_id": scope.organization_id, "environment_id": scope.environment_id},
                strict=True,
            )
        except Exception:
            raise HTTPMockError("invalid_request") from None

    def authorize(self, scope: Scope, permission: str, connection_id: str) -> HTTPMockGrant:
        selected = self._scope(scope)
        try:
            TypeAdapter(Identifier).validate_python(connection_id, strict=True)
        except Exception:
            raise HTTPMockError("invalid_request") from None
        if (
            type(permission) is not str
            or permission != "connector.mock.execute"
            or (selected.organization_id, selected.environment_id) not in self._dev_scopes
        ):
            raise HTTPMockError("forbidden") from None
        try:
            actor = self._worker.authorize(
                Scope(
                    organization_id=selected.organization_id,
                    environment_id=selected.environment_id,
                ),
                "worker.execute",
                connection_id,
            )
        except Exception as error:
            code = getattr(error, "code", None)
            if isinstance(error, PermissionError) or (
                type(code) is str and code in {"forbidden", "unauthenticated"}
            ):
                raise HTTPMockError("forbidden") from None
            raise HTTPMockError("unavailable") from None
        try:
            if type(actor) is not str:
                raise ValueError
            TypeAdapter(Identifier).validate_python(actor, strict=True)
        except Exception:
            raise HTTPMockError("invalid_provider_result") from None
        return HTTPMockGrant(scope=selected, actor_id=actor, environment_kind="dev")
