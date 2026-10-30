"""Explicit, deterministic Dev-only HTTP mocks without a live execution fallback."""

import asyncio
import inspect
import math

from pydantic import TypeAdapter

from nexusflow.contracts.models import Digest, Identifier, Scope
from nexusflow.modules.connectors.attempt_policy import checked_attempt
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.mock_scenarios import checked_scenario, scenario_digest
from nexusflow.modules.connectors.mock_types import (
    HTTPMockError,
    HTTPMockGrant,
    HTTPMockPolicy,
    HTTPMockScenarios,
)
from nexusflow.modules.connectors.policy import request_url
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPAttemptResult


class HTTPMockExecutor:
    """Use trusted Dev policy and private pinned fixtures; never select live adapters."""

    def __init__(
        self,
        policy: HTTPMockPolicy,
        scenarios: HTTPMockScenarios,
        *,
        timeout_seconds: float = 5,
        max_request_bytes: int = 1048576,
        max_response_bytes: int = 1048576,
        max_header_bytes: int = 16384,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 30
            or not math.isfinite(timeout_seconds)
            or any(
                type(limit) is not int or not 0 <= limit <= 4194304
                for limit in (max_request_bytes, max_response_bytes)
            )
            or type(max_header_bytes) is not int
            or not 1024 <= max_header_bytes <= 65536
        ):
            raise ValueError("Invalid HTTP mock limits")
        self.policy = policy
        self.scenarios = scenarios
        self.timeout_seconds = float(timeout_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.max_header_bytes = max_header_bytes

    @staticmethod
    def _scope(value: Scope) -> Scope:
        if type(value) is not Scope:
            raise ValueError
        return Scope.model_validate(
            {"organization_id": value.organization_id, "environment_id": value.environment_id},
            strict=True,
        )

    @staticmethod
    def _checkpoint(deadline: asyncio.Timeout, cancellation_count: int) -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling() > cancellation_count:
            raise asyncio.CancelledError
        expires = deadline.when()
        if deadline.expired() or (
            expires is not None and asyncio.get_running_loop().time() >= expires
        ):
            raise TimeoutError

    def _authorize(self, scope: Scope, connection_id: str) -> None:
        try:
            grant: object = self.policy.authorize(
                self._scope(scope), "connector.mock.execute", connection_id
            )
        except Exception as error:
            code = getattr(error, "code", None)
            if isinstance(error, PermissionError) or (
                type(code) is str and code in {"forbidden", "unauthenticated"}
            ):
                raise HTTPMockError("forbidden") from None
            raise HTTPMockError("unavailable") from None
        try:
            if inspect.iscoroutine(grant):
                grant.close()
                raise ValueError
            if type(grant) is not HTTPMockGrant:
                raise ValueError
            if self._scope(grant.scope) != scope or type(grant.actor_id) is not str:
                raise ValueError
            TypeAdapter(Identifier).validate_python(grant.actor_id, strict=True)
            if type(grant.environment_kind) is not str or grant.environment_kind not in {
                "dev",
                "uat",
                "prod",
            }:
                raise ValueError
        except Exception:
            raise HTTPMockError("invalid_provider_result") from None
        if grant.environment_kind != "dev":
            raise HTTPMockError("forbidden") from None

    async def execute(
        self,
        scope: Scope,
        destination: HTTPDestinationRequest,
        request: HTTPAttemptRequest,
        *,
        fixture_digest: str,
    ) -> HTTPAttemptResult:
        try:
            scope = self._scope(scope)
            if type(destination) is not HTTPDestinationRequest:
                raise ValueError
            destination = HTTPDestinationRequest.model_validate(
                {
                    "connection_id": destination.connection_id,
                    "connection_revision": destination.connection_revision,
                    "operation_id": destination.operation_id,
                    "path": destination.path,
                },
                strict=True,
            )
            request_url("https://mock.example.com", destination.path)
            request = checked_attempt(request, self.max_request_bytes)
            if type(fixture_digest) is not str:
                raise ValueError
            fixture_digest = TypeAdapter(Digest).validate_python(fixture_digest, strict=True)
        except Exception:
            raise HTTPMockError("invalid_request") from None

        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                self._authorize(scope, destination.connection_id)
                self._checkpoint(deadline, cancellation_count)
                value = await self.scenarios.get(
                    self._scope(scope), destination.connection_id, destination.connection_revision
                )
                self._checkpoint(deadline, cancellation_count)
                self._authorize(scope, destination.connection_id)
                self._checkpoint(deadline, cancellation_count)
                if value is None:
                    raise HTTPMockError("not_found")
                scenario = checked_scenario(
                    value,
                    max_request_bytes=self.max_request_bytes,
                    max_response_bytes=self.max_response_bytes,
                    max_header_bytes=self.max_header_bytes,
                )
                if (
                    scenario.scope != scope
                    or scenario.connection_id != destination.connection_id
                    or scenario.connection_revision != destination.connection_revision
                ):
                    raise HTTPMockError("invalid_provider_result")
                if (
                    scenario_digest(scenario) != fixture_digest
                    or scenario.expected_path != destination.path
                    or scenario.expected_request != request
                ):
                    raise HTTPMockError("fixture_mismatch")
                self._checkpoint(deadline, cancellation_count)
                return HTTPAttemptResult(
                    destination.operation_id, scenario.status_code, scenario.headers, scenario.body
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise HTTPMockError("timeout") from None
        except HTTPMockError as error:
            # Reconstruct so provider-mutated messages and delivery flags never escape.
            raise HTTPMockError(error.code) from None
        except Exception:
            raise HTTPMockError("unavailable") from None
