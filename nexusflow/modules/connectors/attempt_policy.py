"""Pure request snapshot validation shared by transport and coordinator."""

from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPTransportError


def checked_attempt(request: HTTPAttemptRequest, max_request_bytes: int) -> HTTPAttemptRequest:
    if (
        type(request) is not HTTPAttemptRequest
        or type(request.method) is not str
        or request.method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
        or type(request.body) is not bytes
        or len(request.body) > max_request_bytes
        or type(request.content_type) is not str
        or request.content_type
        not in {"application/json", "text/plain", "application/octet-stream"}
        or (request.method in {"GET", "HEAD"} and request.body)
        or (
            request.authorization is not None
            and (
                type(request.authorization) is not str
                or not request.authorization.startswith("Bearer ")
                or not 8 <= len(request.authorization) <= 8200
                or any(ord(char) < 32 or ord(char) == 127 for char in request.authorization)
            )
        )
    ):
        raise HTTPTransportError("invalid_request") from None
    return HTTPAttemptRequest(
        request.method, request.body, request.content_type, request.authorization
    )
