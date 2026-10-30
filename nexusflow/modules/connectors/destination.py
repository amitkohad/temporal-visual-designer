"""Worker-authorized, pinned HTTP destination planning without dispatch or secrets."""

import asyncio
import json
import math

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.destination_types import (
    ApprovedHTTPDestination,
    DestinationError,
    HTTPDestinationRequest,
    IPv4Resolver,
    WorkerDestinationAuthorization,
)
from nexusflow.modules.connectors.policy import (
    checked_ipv4,
    checked_url,
    redirect_url,
    request_url,
)
from nexusflow.modules.connectors.service import ConnectionService
from nexusflow.modules.connectors.types import (
    ConnectionRecord,
    ConnectionRepository,
    HTTPConnectionWrite,
)
from nexusflow.modules.project_versioning.canonical import canonical, digest


class HTTPDestinationPlanner:
    def __init__(
        self,
        repository: ConnectionRepository,
        authorization: WorkerDestinationAuthorization,
        resolver: IPv4Resolver,
        *,
        dns_timeout_seconds: float = 5.0,
        max_redirects: int = 5,
    ) -> None:
        if (
            type(dns_timeout_seconds) not in (int, float)
            or not math.isfinite(dns_timeout_seconds)
            or not 0 < dns_timeout_seconds <= 30
            or type(max_redirects) is not int
            or not 0 <= max_redirects <= 10
        ):
            raise ValueError("Invalid HTTP destination limits")
        self.repository = repository
        self.authorization = authorization
        self.resolver = resolver
        self.dns_timeout_seconds = float(dns_timeout_seconds)
        self.max_redirects = max_redirects

    @staticmethod
    def _request(
        scope: Scope, request: HTTPDestinationRequest
    ) -> tuple[Scope, HTTPDestinationRequest]:
        try:
            selected_scope = Scope.model_validate(
                {
                    "organization_id": scope.organization_id,
                    "environment_id": scope.environment_id,
                },
                strict=True,
            )
            selected_request = HTTPDestinationRequest.model_validate(
                {
                    "connection_id": request.connection_id,
                    "connection_revision": request.connection_revision,
                    "operation_id": request.operation_id,
                    "path": request.path,
                },
                strict=True,
            )
            return selected_scope, selected_request
        except Exception:
            raise DestinationError("invalid_request") from None

    def _connection(self, scope: Scope, request: HTTPDestinationRequest) -> HTTPConnectionWrite:
        try:
            actor = self.authorization.authorize(scope, "worker.execute", request.connection_id)
            TypeAdapter(Identifier).validate_python(actor, strict=True)
        except Exception as error:
            code = getattr(error, "code", None)
            if isinstance(error, PermissionError) or (
                type(code) is str
                and code
                in {
                    "forbidden",
                    "unauthenticated",
                }
            ):
                raise DestinationError("forbidden") from None
            raise DestinationError("unavailable") from None

        try:
            record = self.repository.get(scope, request.connection_id, request.connection_revision)
            if not isinstance(record, ConnectionRecord):
                raise ValueError
            metadata = ConnectionService._metadata(
                record.metadata, scope, request.connection_id, request.connection_revision
            )
            encoded = record.definition_json
            if type(encoded) is not str or len(encoded.encode("utf-8")) > 16384:
                raise ValueError

            def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            def reject(_value: str) -> None:
                raise ValueError

            value = json.loads(encoded, object_pairs_hook=unique, parse_constant=reject)
            if type(value) is not dict:
                raise ValueError
            definition = HTTPConnectionWrite.model_validate_json(canonical(value))
            if digest(
                definition.model_dump(mode="json")
            ) != metadata.definition_digest or metadata.has_secret_reference != (
                definition.secret_reference is not None
            ):
                raise ValueError
            return definition
        except Exception:
            raise DestinationError("unavailable") from None

    async def _approve(
        self,
        scope: Scope,
        request: HTTPDestinationRequest,
        definition: HTTPConnectionWrite,
        url: str,
        redirect_count: int,
    ) -> ApprovedHTTPDestination:
        canonical_url, origin, host, port = checked_url(url, definition.allowed_origins)
        try:
            async with asyncio.timeout(self.dns_timeout_seconds) as deadline:
                addresses = await self.resolver.resolve_ipv4(host)
                expires = deadline.when()
                if deadline.expired() or (
                    expires is not None and asyncio.get_running_loop().time() >= expires
                ):
                    raise TimeoutError
        except Exception:
            raise DestinationError("unavailable") from None
        approved = checked_ipv4(addresses)
        return ApprovedHTTPDestination(
            scope=scope,
            connection_id=request.connection_id,
            connection_revision=request.connection_revision,
            definition_digest=digest(definition.model_dump(mode="json")),
            operation_id=request.operation_id,
            url=canonical_url,
            origin=origin,
            host=host,
            port=port,
            ipv4_addresses=approved,
            redirect_count=redirect_count,
        )

    async def prepare(
        self, scope: Scope, request: HTTPDestinationRequest
    ) -> ApprovedHTTPDestination:
        scope, request = self._request(scope, request)
        definition = self._connection(scope, request)
        url = request_url(definition.base_origin, request.path)
        return await self._approve(scope, request, definition, url, 0)

    async def prepare_redirect(
        self,
        scope: Scope,
        request: HTTPDestinationRequest,
        previous: ApprovedHTTPDestination,
        location: str,
    ) -> ApprovedHTTPDestination:
        scope, request = self._request(scope, request)
        definition = self._connection(scope, request)
        try:
            if (
                not isinstance(previous, ApprovedHTTPDestination)
                or not isinstance(previous.scope, Scope)
                or previous.scope != scope
                or previous.connection_id != request.connection_id
                or previous.connection_revision != request.connection_revision
                or previous.operation_id != request.operation_id
                or previous.definition_digest != digest(definition.model_dump(mode="json"))
                or type(previous.port) is not int
                or not 1 <= previous.port <= 65535
                or type(previous.redirect_count) is not int
                or not 0 <= previous.redirect_count <= self.max_redirects
            ):
                raise ValueError
            previous_url, origin, host, port = checked_url(previous.url, definition.allowed_origins)
            if (
                previous_url != previous.url
                or origin != previous.origin
                or host != previous.host
                or port != previous.port
                or checked_ipv4(previous.ipv4_addresses) != previous.ipv4_addresses
            ):
                raise ValueError
        except Exception:
            raise DestinationError("invalid_request") from None
        if previous.redirect_count >= self.max_redirects:
            raise DestinationError("redirect_limit")
        target = redirect_url(previous.url, location)
        return await self._approve(scope, request, definition, target, previous.redirect_count + 1)
