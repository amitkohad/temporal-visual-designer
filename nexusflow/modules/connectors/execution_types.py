"""Internal single-attempt coordination contracts; raw data stays private."""

from typing import Literal, Protocol, get_args

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.destination_types import (
    ApprovedHTTPDestination,
    HTTPDestinationRequest,
)


class CredentialFreeDestinationPlanner(Protocol):
    async def prepare_credential_free(
        self, scope: Scope, request: HTTPDestinationRequest
    ) -> ApprovedHTTPDestination: ...

    def revalidate_credential_free(
        self, scope: Scope, request: HTTPDestinationRequest, approved: ApprovedHTTPDestination
    ) -> None: ...


HTTPExecutionErrorCode = Literal[
    "invalid_request",
    "forbidden",
    "credentials_required",
    "blocked_destination",
    "unavailable",
    "timeout",
    "protocol_error",
    "response_too_large",
    "invalid_provider_result",
]


class HTTPExecutionError(Exception):
    def __init__(
        self, code: HTTPExecutionErrorCode, *, delivery_may_have_occurred: bool = False
    ) -> None:
        self.code: HTTPExecutionErrorCode = (
            code
            if type(code) is str and code in get_args(HTTPExecutionErrorCode)
            else "unavailable"
        )
        self.delivery_may_have_occurred = (
            delivery_may_have_occurred if type(delivery_may_have_occurred) is bool else True
        )
        self.retryable = False
        super().__init__("HTTP execution attempt failed")
