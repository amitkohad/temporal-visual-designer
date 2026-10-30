"""Internal HTTP JSON activity mapping; raw provider results never become outputs."""

import asyncio
import math
import re
from typing import cast, get_args

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.activity_types import (
    HTTPActivityError,
    HTTPActivityErrorCode,
    HTTPActivityLiveExecutor,
    HTTPActivityMockExecutor,
    HTTPJSONActivityInput,
    HTTPJSONActivityOutput,
)
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.execution_types import HTTPExecutionError
from nexusflow.modules.connectors.mock_types import HTTPMockError
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPAttemptResult
from nexusflow.modules.project_versioning.canonical import canonical


class _InvalidResult(Exception):
    pass


class HTTPJSONActivityAdapter:
    def __init__(
        self,
        *,
        live: HTTPActivityLiveExecutor | None = None,
        mock: HTTPActivityMockExecutor | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 30
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("Invalid HTTP JSON activity timeout")
        self.live = live
        self.mock = mock
        self.timeout_seconds = float(timeout_seconds)

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

    @staticmethod
    def _code(value: object) -> HTTPActivityErrorCode:
        return (
            cast(HTTPActivityErrorCode, value)
            if type(value) is str and value in get_args(HTTPActivityErrorCode)
            else "unavailable"
        )

    @staticmethod
    def _inputs(value: HTTPJSONActivityInput | dict[str, object]) -> HTTPJSONActivityInput:
        raw: dict[str, object]
        if type(value) is HTTPJSONActivityInput:
            raw = {
                "connection_id": value.connection_id,
                "connection_revision": value.connection_revision,
                "execution_mode": value.execution_mode,
                "fixture_digest": value.fixture_digest,
                "method": value.method,
                "path": value.path,
                "json_body": value.json_body,
            }
        elif type(value) is dict:
            raw = value
        else:
            raise ValueError
        selected = HTTPJSONActivityInput.model_validate(raw, strict=True)
        # Encode only validated native values; never invoke a constructed model's serializer.
        encoded = canonical(
            {
                "connection_id": selected.connection_id,
                "connection_revision": selected.connection_revision,
                "execution_mode": selected.execution_mode,
                "fixture_digest": selected.fixture_digest,
                "method": selected.method,
                "path": selected.path,
                "json_body": selected.json_body,
            }
        )
        return HTTPJSONActivityInput.model_validate_json(encoded, strict=True)

    @staticmethod
    def _result(value: HTTPAttemptResult, operation_id: str, method: str) -> int:
        try:
            if (
                type(value) is not HTTPAttemptResult
                or type(value.operation_id) is not str
                or value.operation_id != operation_id
                or type(value.status_code) is not int
                or not 200 <= value.status_code <= 599
                or type(value.body) is not bytes
                or len(value.body) > 1048576
                or ((method == "HEAD" or value.status_code in {204, 304}) and value.body)
                or type(value.headers) is not tuple
                or len(value.headers) > 100
            ):
                raise ValueError
            size = 0
            for header in value.headers:
                if (
                    type(header) is not tuple
                    or len(header) != 2
                    or type(header[0]) is not str
                    or type(header[1]) is not str
                    or size + len(header[0]) + len(header[1]) + 4 > 16384
                    or re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", header[0]) is None
                    or any(ord(char) < 32 or ord(char) == 127 for char in header[1])
                ):
                    raise ValueError
                size += len(header[0].encode("ascii")) + len(header[1].encode("latin-1")) + 4
                if size > 16384:
                    raise ValueError
            return value.status_code
        except Exception:
            raise _InvalidResult from None

    async def execute(
        self, scope: Scope, operation_id: str, inputs: HTTPJSONActivityInput | dict[str, object]
    ) -> HTTPJSONActivityOutput:
        try:
            if type(scope) is not Scope or type(operation_id) is not str:
                raise ValueError
            scope = Scope.model_validate(
                {"organization_id": scope.organization_id, "environment_id": scope.environment_id},
                strict=True,
            )
            operation_id = TypeAdapter(Identifier).validate_python(operation_id, strict=True)
            selected = self._inputs(inputs)
            destination = HTTPDestinationRequest(
                connection_id=selected.connection_id,
                connection_revision=selected.connection_revision,
                operation_id=operation_id,
                path=selected.path,
            )
            request = HTTPAttemptRequest(
                method=selected.method,
                body=b""
                if selected.json_body is None
                else canonical(selected.json_body).encode("utf-8"),
                content_type="application/json",
            )
        except Exception:
            raise HTTPActivityError("invalid_request") from None

        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        live_entered = False
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                self._checkpoint(deadline, cancellation_count)
                if selected.execution_mode == "live":
                    if self.live is None:
                        raise HTTPActivityError("unavailable")
                    live_entered = True
                    result = await self.live.execute(scope, destination, request)
                else:
                    if self.mock is None or selected.fixture_digest is None:
                        raise HTTPActivityError("unavailable")
                    result = await self.mock.execute(
                        scope, destination, request, fixture_digest=selected.fixture_digest
                    )
                self._checkpoint(deadline, cancellation_count)
                status = self._result(result, operation_id, selected.method)
                self._checkpoint(deadline, cancellation_count)
                return HTTPJSONActivityOutput(
                    operation_id=operation_id,
                    status_code=status,
                    execution_mode=selected.execution_mode,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise HTTPActivityError("timeout", delivery_may_have_occurred=live_entered) from None
        except _InvalidResult:
            raise HTTPActivityError(
                "invalid_provider_result", delivery_may_have_occurred=live_entered
            ) from None
        except HTTPExecutionError as error:
            flag = getattr(error, "delivery_may_have_occurred", None)
            raise HTTPActivityError(
                self._code(error.code) if live_entered else "unavailable",
                delivery_may_have_occurred=(flag if type(flag) is bool else True)
                if live_entered
                else False,
            ) from None
        except HTTPMockError as error:
            raise HTTPActivityError(
                "unavailable" if live_entered else self._code(error.code),
                delivery_may_have_occurred=live_entered,
            ) from None
        except HTTPActivityError as error:
            raise HTTPActivityError(
                self._code(error.code), delivery_may_have_occurred=live_entered
            ) from None
        except Exception:
            raise HTTPActivityError(
                "unavailable", delivery_may_have_occurred=live_entered
            ) from None
