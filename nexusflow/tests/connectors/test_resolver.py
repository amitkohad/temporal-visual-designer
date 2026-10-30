"""System resolver boundary tests replace getaddrinfo; no public DNS is used."""

import asyncio
import socket
import time
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from nexusflow.modules.connectors.destination_types import DestinationError
from nexusflow.modules.connectors.resolver import AsyncIPv4Resolver


def answer(ip="8.8.8.8"):
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))


@pytest_asyncio.fixture
async def provider(monkeypatch):
    selected = AsyncMock(return_value=[answer()])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", selected)
    return selected


@pytest.mark.asyncio
async def test_system_call_is_ipv4_tcp_only_and_results_are_unique_and_canonical(provider):
    provider.return_value = [answer("9.9.9.9"), answer(), answer("1.1.1.1"), answer()]
    assert await AsyncIPv4Resolver().resolve_ipv4("api.example.com") == (
        "1.1.1.1",
        "8.8.8.8",
        "9.9.9.9",
    )
    provider.assert_awaited_once_with(
        "api.example.com",
        None,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        None,
        1,
        True,
        "",
        "localhost",
        "API.example.com",
        "api.example.com.",
        "127.0.0.1",
        "[::1]",
        "::1",
        "api.example.com:443",
        "api.example.com:8443",
        "api.example.com/",
        "api.example.com/path",
        "user@api.example.com",
        "api.example.com?private=secret",
        "api.example.com#private",
        "api.example.com\\private",
        "api.example.com\n",
        "api.exämple.com",
        "https://api.example.com",
        "a" * 254,
        "-bad.example.com",
    ],
)
async def test_invalid_or_noncanonical_hosts_fail_before_system_lookup(provider, host):
    with pytest.raises(DestinationError) as failure:
        await AsyncIPv4Resolver().resolve_ipv4(host)
    assert failure.value.code == "invalid_request"
    assert not failure.value.retryable
    provider.assert_not_called()


@pytest.mark.parametrize("value", [None, True, False, "5", 0, -1, 30.1, float("inf"), float("nan")])
def test_timeout_configuration_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="Invalid HTTP resolver timeout"):
        AsyncIPv4Resolver(timeout_seconds=value)


@pytest.mark.parametrize("value", [0.01, 5, 30])
def test_timeout_configuration_accepts_bounded_numbers(value):
    assert AsyncIPv4Resolver(timeout_seconds=value).timeout_seconds == float(value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        None,
        [],
        (),
        {},
        "8.8.8.8",
        [answer()] * 17,
        [None],
        [list(answer())],
        [(2, 1)],
        [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
        [(2, socket.SOCK_DGRAM, 6, "", ("8.8.8.8", 0))],
        [(2, 1, 17, "", ("8.8.8.8", 0))],
        [(2, 1, 6, None, ("8.8.8.8", 0))],
        [(2, 1, 6, "private-provider-value", ("8.8.8.8", 0))],
        [(2, 1, 6, "", ["8.8.8.8", 0])],
        [(2, 1, 6, "", ("8.8.8.8",))],
        [(2, 1, 6, "", ("8.8.8.8", 443))],
        [(2, 1, 6, "", ("8.8.8.8", False))],
        [(2, 1, 6, "", (b"8.8.8.8", 0))],
        [answer(), None],
    ],
)
async def test_malformed_or_oversized_system_answers_fail_closed(provider, answers):
    provider.return_value = answers
    with pytest.raises(DestinationError) as failure:
        await AsyncIPv4Resolver().resolve_ipv4("api.example.com")
    assert failure.value.code == "unavailable"
    assert "private-provider-value" not in str(failure.value) + repr(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "168.63.129.16",
        "100.64.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::ffff:8.8.8.8",
        "008.8.8.8",
        "8.8.8.8 ",
        "not-an-address",
    ],
)
async def test_mixed_or_private_answers_are_never_silently_filtered(provider, ip):
    provider.return_value = [answer(), answer(ip)]
    with pytest.raises(DestinationError) as failure:
        await AsyncIPv4Resolver().resolve_ipv4("api.example.com")
    assert failure.value.code == "blocked_destination"
    assert not failure.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [OSError("private-provider-secret"), RuntimeError("private-provider-secret")]
)
async def test_system_errors_are_normalized_without_provider_text(provider, error):
    provider.side_effect = error
    with pytest.raises(DestinationError) as failure:
        await AsyncIPv4Resolver().resolve_ipv4("api.example.com")
    assert failure.value.code == "unavailable"
    assert failure.value.__suppress_context__
    assert "private-provider-secret" not in str(failure.value) + repr(failure.value)


@pytest.mark.asyncio
async def test_stalled_lookup_is_bounded_and_cancelled(provider):
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider.side_effect = stalled
    with pytest.raises(DestinationError) as failure:
        await AsyncIPv4Resolver(timeout_seconds=0.01).resolve_ipv4("api.example.com")
    assert failure.value.code == "unavailable"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_caller_cancellation_propagates(provider):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def stalled(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    provider.side_effect = stalled
    task = asyncio.create_task(AsyncIPv4Resolver().resolve_ipv4("api.example.com"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_provider_suppressing_timeout_cannot_return_late_answer(provider):
    async def late(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return [answer()]

    provider.side_effect = late
    with pytest.raises(DestinationError) as failure:
        await AsyncIPv4Resolver(timeout_seconds=0.01).resolve_ipv4("api.example.com")
    assert failure.value.code == "unavailable"


@pytest.mark.asyncio
async def test_provider_suppressing_caller_cancellation_cannot_return_answer(provider):
    started = asyncio.Event()

    async def suppress(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return [answer()]

    provider.side_effect = suppress
    task = asyncio.create_task(AsyncIPv4Resolver().resolve_ipv4("api.example.com"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_provider_delaying_timeout_callback_cannot_return_late_answer(provider):
    async def late(*args, **kwargs):
        time.sleep(0.02)
        return [answer()]

    provider.side_effect = late
    with pytest.raises(DestinationError) as failure:
        await AsyncIPv4Resolver(timeout_seconds=0.005).resolve_ipv4("api.example.com")
    assert failure.value.code == "unavailable"
