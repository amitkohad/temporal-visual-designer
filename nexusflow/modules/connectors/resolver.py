"""Bounded system A-record resolution for internal HTTP destination preflight."""

import asyncio
import math
import socket

from nexusflow.modules.connectors.destination_types import DestinationError
from nexusflow.modules.connectors.policy import checked_ipv4
from nexusflow.modules.connectors.types import https_origin


class AsyncIPv4Resolver:
    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("Invalid HTTP resolver timeout")
        self.timeout_seconds = float(timeout_seconds)

    async def resolve_ipv4(self, host: str) -> tuple[str, ...]:
        try:
            if (
                type(host) is not str
                or not 1 <= len(host) <= 253
                or ":" in host
                or https_origin("https://" + host) != "https://" + host
            ):
                raise ValueError
        except Exception:
            raise DestinationError("invalid_request") from None

        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        cancellation_count = task.cancelling() if task is not None else 0
        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:
                answers = await loop.getaddrinfo(
                    host,
                    None,
                    family=socket.AF_INET,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
                expires = deadline.when()
                if deadline.expired() or (expires is not None and loop.time() >= expires):
                    raise TimeoutError
                if task is not None and task.cancelling() > cancellation_count:
                    raise asyncio.CancelledError
        except Exception:
            raise DestinationError("unavailable") from None

        try:
            if type(answers) not in (list, tuple) or not 1 <= len(answers) <= 16:
                raise ValueError
            addresses: list[str] = []
            for answer in answers:
                if type(answer) is not tuple or len(answer) != 5:
                    raise ValueError
                family, kind, protocol, canonical_name, address = answer
                if (
                    type(family) not in (int, socket.AddressFamily)
                    or family != socket.AF_INET
                    or type(kind) not in (int, socket.SocketKind)
                    or kind != socket.SOCK_STREAM
                    or type(protocol) is not int
                    or protocol != socket.IPPROTO_TCP
                    or type(canonical_name) is not str
                    or canonical_name != ""
                    or type(address) is not tuple
                    or len(address) != 2
                    or type(address[0]) is not str
                    or type(address[1]) is not int
                    or address[1] != 0
                ):
                    raise ValueError
                addresses.append(address[0])
        except Exception:
            raise DestinationError("unavailable") from None
        return checked_ipv4(tuple(sorted(set(addresses))))
