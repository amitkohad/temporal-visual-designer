"""Bounded request buffering and server-owned correlation without payload logging."""

from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def error_response(request_id: str, status: int, code: str, message: str) -> JSONResponse:
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {"schema_version": "0.1", "request_id": request_id, "code": code, "message": message},
        status_code=status,
        headers=headers,
    )


class ApiBoundary:
    def __init__(self, app: ASGIApp, maximum_body_bytes: int = 6_000_000) -> None:
        if maximum_body_bytes < 1:
            raise ValueError("Body limit must be positive")
        self.app = app
        self.maximum = maximum_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        headers = scope.get("headers", [])

        async def reject(status: int, code: str, message: str) -> None:
            await error_response(request_id, status, code, message)(scope, receive, send)

        if scope.get("path", "").startswith("/api/"):
            authorization = [value for name, value in headers if name.lower() == b"authorization"]
            if (
                len(authorization) != 1
                or not authorization[0].lower().startswith(b"bearer ")
                or not 1 <= len(authorization[0][7:]) <= 32768
            ):
                await reject(401, "unauthenticated", "A bearer token is required")
                return
        if any(
            name.lower() == b"content-encoding" and value.lower() != b"identity"
            for name, value in headers
        ):
            await reject(415, "invalid_input", "Encoded request bodies are unsupported")
            return
        lengths = [value for name, value in headers if name.lower() == b"content-length"]
        if lengths and (len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isdigit()):
            await reject(400, "invalid_input", "Invalid content length")
            return
        if lengths and int(lengths[0]) > self.maximum:
            await reject(413, "invalid_input", "Request body exceeds the size limit")
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.maximum:
                await reject(413, "invalid_input", "Request body exceeds the size limit")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def buffered_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def correlated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in {b"x-request-id", b"cache-control"}
                ]
                response_headers.extend(
                    [(b"x-request-id", request_id.encode()), (b"cache-control", b"no-store")]
                )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, buffered_receive, correlated_send)
