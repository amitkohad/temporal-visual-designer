"""Freshly authorized, explicitly enabled Dev HTTP JSON fixture administration."""

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.mock_admin_types import (
    HTTPMockFixtureAdminError,
    HTTPMockFixtureMetadata,
    HTTPMockFixtureStore,
    HTTPMockFixtureWrite,
)
from nexusflow.modules.connectors.mock_scenarios import checked_scenario, scenario_digest
from nexusflow.modules.connectors.mock_storage_codec import encode_scenario
from nexusflow.modules.connectors.mock_storage_types import (
    HTTPMockFixtureReceipt,
    HTTPMockStorageError,
)
from nexusflow.modules.connectors.mock_types import HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.types import ProjectAuthorization


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope.model_validate(
        {"organization_id": value.organization_id, "environment_id": value.environment_id},
        strict=True,
    )


def _identifier(value: str) -> str:
    if type(value) is not str:
        raise ValueError
    return TypeAdapter(Identifier).validate_python(value, strict=True)


class HTTPMockFixtureService:
    """Private fixture bodies enter through writes and never leave as public metadata.

    The explicitly configured Dev scopes are host policy, not identity discovery.
    The store remains a trusted host port, independently validated at this boundary.
    """

    def __init__(
        self,
        store: HTTPMockFixtureStore,
        authorization: ProjectAuthorization,
        dev_scopes: tuple[Scope, ...],
    ) -> None:
        try:
            if type(dev_scopes) is not tuple:
                raise ValueError
            keys: set[tuple[str, str]] = set()
            for value in dev_scopes:
                selected = _scope(value)
                key = (selected.organization_id, selected.environment_id)
                if selected.environment_id != "dev" or key in keys:
                    raise ValueError
                keys.add(key)
        except Exception:
            raise ValueError("Invalid HTTP mock fixture administration configuration") from None
        self.store = store
        self.authorization = authorization
        self._dev_keys = frozenset(keys)

    def _authorize(self, scope: Scope) -> None:
        try:
            actor = self.authorization.authorize(
                scope.model_copy(deep=True), "connection.manage", "connections"
            )
            _identifier(actor)
        except IdentityError:
            raise
        except PermissionError:
            raise HTTPMockFixtureAdminError("forbidden") from None
        except Exception:
            raise HTTPMockFixtureAdminError("unavailable") from None
        if (
            scope.environment_id != "dev"
            or (scope.organization_id, scope.environment_id) not in self._dev_keys
        ):
            raise HTTPMockFixtureAdminError("forbidden") from None

    def _context(self, scope: Scope, connection_id: str, revision: str) -> Scope:
        try:
            selected = _scope(scope)
        except Exception:
            raise HTTPMockFixtureAdminError("invalid_request") from None
        self._authorize(selected)
        try:
            _identifier(connection_id)
            _identifier(revision)
        except Exception:
            raise HTTPMockFixtureAdminError("invalid_request") from None
        return selected

    @staticmethod
    def _metadata(value: HTTPMockScenario) -> HTTPMockFixtureMetadata:
        encoded = encode_scenario(value)
        return HTTPMockFixtureMetadata(
            scope=_scope(value.scope),
            connection_id=value.connection_id,
            connection_revision=value.connection_revision,
            fixture_digest=scenario_digest(value),
            stored_bytes=len(encoded.encode("utf-8")),
        )

    @staticmethod
    def _receipt(value: HTTPMockFixtureReceipt) -> HTTPMockFixtureMetadata:
        if type(value) is not HTTPMockFixtureReceipt:
            raise ValueError
        return HTTPMockFixtureMetadata.model_validate(
            {
                "scope": _scope(value.scope),
                "connection_id": value.connection_id,
                "connection_revision": value.connection_revision,
                "fixture_digest": value.fixture_digest,
                "stored_bytes": value.stored_bytes,
            },
            strict=True,
        )

    @staticmethod
    def _scenario(
        scope: Scope, connection_id: str, revision: str, payload: HTTPMockFixtureWrite
    ) -> HTTPMockScenario:
        if type(payload) is not HTTPMockFixtureWrite:
            raise ValueError
        validated = HTTPMockFixtureWrite.model_validate(
            {
                "method": payload.method,
                "path": payload.path,
                "json_body": payload.json_body,
                "status_code": payload.status_code,
                "response_json_body": payload.response_json_body,
            },
            strict=True,
        )
        snapshot = HTTPMockFixtureWrite.model_validate_json(
            canonical(validated.model_dump(mode="json")), strict=True
        )
        return HTTPMockScenario(
            scope=scope.model_copy(deep=True),
            connection_id=connection_id,
            connection_revision=revision,
            expected_path=snapshot.path,
            expected_request=HTTPAttemptRequest(
                method=snapshot.method,
                body=b""
                if snapshot.json_body is None
                else canonical(snapshot.json_body).encode("utf-8"),
                content_type="application/json",
            ),
            status_code=snapshot.status_code,
            headers=(),
            body=b""
            if snapshot.response_json_body is None
            else canonical(snapshot.response_json_body).encode("utf-8"),
        )

    def put(
        self,
        scope: Scope,
        connection_id: str,
        connection_revision: str,
        payload: HTTPMockFixtureWrite,
    ) -> HTTPMockFixtureMetadata:
        selected = self._context(scope, connection_id, connection_revision)
        try:
            scenario = self._scenario(selected, connection_id, connection_revision, payload)
            expected = self._metadata(scenario)
        except Exception:
            raise HTTPMockFixtureAdminError("invalid_request") from None
        try:
            receipt = self.store.put(scenario)
        except HTTPMockStorageError as error:
            code = error.code
            if type(code) is not str or code not in {
                "invalid_request",
                "conflict",
                "limit_exceeded",
                "unavailable",
            }:
                raise HTTPMockFixtureAdminError("unavailable") from None
            raise HTTPMockFixtureAdminError(code) from None
        except Exception:
            raise HTTPMockFixtureAdminError("unavailable") from None
        self._authorize(selected)
        try:
            result = self._receipt(receipt)
            if result != expected:
                raise ValueError
            return result
        except Exception:
            raise HTTPMockFixtureAdminError("unavailable") from None

    async def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> HTTPMockFixtureMetadata:
        selected = self._context(scope, connection_id, connection_revision)
        try:
            stored = await self.store.get(
                selected.model_copy(deep=True), connection_id, connection_revision
            )
        except Exception:
            raise HTTPMockFixtureAdminError("unavailable") from None
        self._authorize(selected)
        if stored is None:
            raise HTTPMockFixtureAdminError("not_found") from None
        try:
            scenario = checked_scenario(
                stored,
                max_request_bytes=4194304,
                max_response_bytes=4194304,
                max_header_bytes=65536,
            )
            if (
                scenario.scope != selected
                or scenario.connection_id != connection_id
                or scenario.connection_revision != connection_revision
            ):
                raise ValueError
            return self._metadata(scenario)
        except Exception:
            raise HTTPMockFixtureAdminError("unavailable") from None
