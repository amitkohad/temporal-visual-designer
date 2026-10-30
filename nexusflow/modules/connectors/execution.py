"""Fresh-authorized, credential-free HTTP attempts without retries or public APIs."""

import asyncio
import inspect
import math
import re
from collections.abc import Callable
from typing import cast, get_args

from pydantic import TypeAdapter

from nexusflow.contracts.models import Digest, Scope
from nexusflow.modules.connectors.attempt_policy import checked_attempt
from nexusflow.modules.connectors.destination_types import (
    ApprovedHTTPDestination,
    DestinationError,
    HTTPDestinationRequest,
)
from nexusflow.modules.connectors.execution_types import (
    CredentialFreeDestinationPlanner,
    HTTPExecutionError,
    HTTPExecutionErrorCode,
)
from nexusflow.modules.connectors.policy import checked_ipv4, checked_url, request_url
from nexusflow.modules.connectors.transport_types import (
    HTTPAttemptRequest,
    HTTPAttemptResult,
    HTTPTransportError,
    HTTPTransportErrorCode,
    PinnedHTTPSender,
)


class _InvalidProviderResult(Exception):
    pass


class HTTPAttemptExecutor:
    """Compose trusted ports; routing records are not public authorization tokens."""

    def __init__(
        self,
        planner: CredentialFreeDestinationPlanner,
        sender: PinnedHTTPSender,
        *,
        timeout_seconds: float = 30,
        max_request_bytes: int = 1048576,
        max_response_bytes: int = 1048576,
        max_header_bytes: int = 16384,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 120
            or not math.isfinite(timeout_seconds)
            or any(
                type(limit) is not int or not 0 <= limit <= 4194304
                for limit in (max_request_bytes, max_response_bytes)
            )
            or type(max_header_bytes) is not int
            or not 1024 <= max_header_bytes <= 65536
        ):
            raise ValueError("Invalid HTTP execution limits")
        self.planner = planner
        self.sender = sender
        self.timeout_seconds = float(timeout_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.max_header_bytes = max_header_bytes

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
    def _code(value: object) -> HTTPExecutionErrorCode:
        return (
            cast(HTTPExecutionErrorCode, value)
            if type(value) is str and value in get_args(HTTPExecutionErrorCode)
            else "unavailable"
        )

    @staticmethod
    def _plan(
        value: ApprovedHTTPDestination, scope: Scope, destination: HTTPDestinationRequest
    ) -> ApprovedHTTPDestination:
        try:
            if (
                type(value) is not ApprovedHTTPDestination
                or type(value.scope) is not Scope
                or Scope.model_validate(
                    {
                        "organization_id": value.scope.organization_id,
                        "environment_id": value.scope.environment_id,
                    },
                    strict=True,
                )
                != scope
                or any(
                    type(field) is not str
                    for field in (
                        value.connection_id,
                        value.connection_revision,
                        value.operation_id,
                        value.definition_digest,
                        value.url,
                        value.origin,
                        value.host,
                    )
                )
                or value.connection_id != destination.connection_id
                or value.connection_revision != destination.connection_revision
                or value.operation_id != destination.operation_id
                or type(value.port) is not int
                or type(value.redirect_count) is not int
                or value.redirect_count != 0
                or type(value.origin) is not str
                or type(value.host) is not str
                or checked_url(value.url, (value.origin,))
                != (value.url, value.origin, value.host, value.port)
                or request_url(value.origin, destination.path) != value.url
                or checked_ipv4(value.ipv4_addresses) != value.ipv4_addresses
            ):
                raise ValueError
            TypeAdapter(Digest).validate_python(value.definition_digest, strict=True)
            return ApprovedHTTPDestination(
                scope=scope,
                connection_id=destination.connection_id,
                connection_revision=destination.connection_revision,
                definition_digest=value.definition_digest,
                operation_id=destination.operation_id,
                url=value.url,
                origin=value.origin,
                host=value.host,
                port=value.port,
                ipv4_addresses=value.ipv4_addresses,
                redirect_count=0,
            )
        except Exception:
            raise _InvalidProviderResult from None

    def _result(
        self, value: HTTPAttemptResult, operation_id: str, method: str
    ) -> HTTPAttemptResult:
        try:
            if (
                type(value) is not HTTPAttemptResult
                or type(value.operation_id) is not str
                or value.operation_id != operation_id
                or type(value.status_code) is not int
                or not 200 <= value.status_code <= 599
                or type(value.headers) is not tuple
                or len(value.headers) > 100
                or type(value.body) is not bytes
                or len(value.body) > self.max_response_bytes
                or ((method == "HEAD" or value.status_code in (204, 304)) and value.body)
            ):
                raise ValueError
            size = 0
            for header in value.headers:
                if (
                    type(header) is not tuple
                    or len(header) != 2
                    or type(header[0]) is not str
                    or re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", header[0]) is None
                    or type(header[1]) is not str
                    or any(ord(char) < 32 or ord(char) == 127 for char in header[1])
                ):
                    raise ValueError
                size += len(header[0].encode("ascii")) + len(header[1].encode("latin-1")) + 4
                if size > self.max_header_bytes:
                    raise ValueError
            return HTTPAttemptResult(operation_id, value.status_code, value.headers, value.body)
        except Exception:
            raise _InvalidProviderResult from None

    def _revalidate(
        self, scope: Scope, destination: HTTPDestinationRequest, approved: ApprovedHTTPDestination
    ) -> None:
        validate = cast(
            Callable[[Scope, HTTPDestinationRequest, ApprovedHTTPDestination], object],
            self.planner.revalidate_credential_free,
        )
        outcome = validate(scope, destination, approved)
        if outcome is not None:
            if inspect.iscoroutine(outcome):
                outcome.close()
            raise _InvalidProviderResult

    async def execute(
        self, scope: Scope, destination: HTTPDestinationRequest, request: HTTPAttemptRequest
    ) -> HTTPAttemptResult:
        try:
            if type(scope) is not Scope or type(destination) is not HTTPDestinationRequest:
                raise ValueError
            scope = Scope.model_validate(
                {"organization_id": scope.organization_id, "environment_id": scope.environment_id},
                strict=True,
            )
            destination = HTTPDestinationRequest.model_validate(
                {
                    "connection_id": destination.connection_id,
                    "connection_revision": destination.connection_revision,
                    "operation_id": destination.operation_id,
                    "path": destination.path,
                },
                strict=True,
            )
            request = checked_attempt(request, self.max_request_bytes)
        except Exception:
            raise HTTPExecutionError("invalid_request") from None

        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        entered_sender = False
        guard_count = 0
        guard_failure: HTTPTransportError | None = None
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                approved = await self.planner.prepare_credential_free(scope, destination)
                self._checkpoint(deadline, cancellation_count)
                approved = self._plan(approved, scope, destination)
                self._revalidate(scope, destination, approved)
                self._checkpoint(deadline, cancellation_count)

                def before_write() -> None:
                    nonlocal guard_count, guard_failure
                    self._checkpoint(deadline, cancellation_count)
                    guard_count += 1
                    if guard_count != 1:
                        guard_failure = HTTPTransportError(
                            "unavailable", delivery_may_have_occurred=True
                        )
                        raise guard_failure
                    try:
                        self._revalidate(scope, destination, approved)
                        self._checkpoint(deadline, cancellation_count)
                    except DestinationError as error:
                        code = self._code(error.code)
                        transport_code: HTTPTransportErrorCode = (
                            cast(HTTPTransportErrorCode, code)
                            if code in get_args(HTTPTransportErrorCode)
                            else "unavailable"
                        )
                        guard_failure = HTTPTransportError(transport_code)
                        raise guard_failure from None
                    except (TimeoutError, asyncio.CancelledError):
                        raise
                    except Exception:
                        guard_failure = HTTPTransportError("unavailable")
                        raise guard_failure from None

                entered_sender = True
                result = await self.sender.send(approved, request, before_write=before_write)
                self._checkpoint(deadline, cancellation_count)
                if guard_count != 1 or guard_failure is not None:
                    raise _InvalidProviderResult
                result = self._result(result, destination.operation_id, request.method)
                self._checkpoint(deadline, cancellation_count)
                return result
        except asyncio.CancelledError:
            raise
        except _InvalidProviderResult:
            raise HTTPExecutionError(
                "invalid_provider_result", delivery_may_have_occurred=entered_sender
            ) from None
        except TimeoutError:
            raise HTTPExecutionError("timeout", delivery_may_have_occurred=entered_sender) from None
        except HTTPTransportError as error:
            flag = getattr(error, "delivery_may_have_occurred", None)
            if guard_count > 1:
                raise HTTPExecutionError(
                    "invalid_provider_result", delivery_may_have_occurred=True
                ) from None
            raise HTTPExecutionError(
                self._code(error.code),
                delivery_may_have_occurred=(flag if type(flag) is bool else entered_sender),
            ) from None
        except DestinationError as error:
            raise HTTPExecutionError(
                "unavailable" if entered_sender else self._code(error.code),
                delivery_may_have_occurred=entered_sender,
            ) from None
        except Exception:
            raise HTTPExecutionError(
                "unavailable", delivery_may_have_occurred=entered_sender
            ) from None
