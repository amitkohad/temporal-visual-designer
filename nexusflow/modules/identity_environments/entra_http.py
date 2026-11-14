"""Trusted, allowlisted JSON GET for Entra JWKS/Graph adapters.

Uses the standard library only. Callers must pass absolute https URLs whose host
is on the allowlist; token-supplied URLs are never accepted by adapters.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlsplit


class EntraHttpError(Exception):
    """Transport or response failure at the Microsoft boundary."""


class JsonHttpGet(Protocol):
    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> dict[str, object]: ...


class AllowlistedJsonHttpGet:
    """Fail-closed HTTPS JSON GET limited to configured Microsoft hosts."""

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        timeout_seconds: float = 5,
        maximum_bytes: int = 1_048_576,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if (
            not isinstance(allowed_hosts, frozenset)
            or not allowed_hosts
            or type(timeout_seconds) not in (int, float)
            or not 0 < float(timeout_seconds) <= 30
            or type(maximum_bytes) is not int
            or not 1024 <= maximum_bytes <= 4_194_304
        ):
            raise ValueError("Invalid Entra HTTP client limits")
        for host in allowed_hosts:
            if not isinstance(host, str) or not host or "/" in host or ":" in host:
                raise ValueError("Invalid allowlisted host")
        context = ssl_context if ssl_context is not None else ssl.create_default_context()
        if (
            not isinstance(context, ssl.SSLContext)
            or context.verify_mode != ssl.CERT_REQUIRED
            or not context.check_hostname
        ):
            raise ValueError("Verified HTTPS context required")
        self.allowed_hosts = allowed_hosts
        self.timeout_seconds = float(timeout_seconds)
        self.maximum_bytes = maximum_bytes
        self.ssl_context = context

    def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> dict[str, object]:
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise EntraHttpError("Invalid URL")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.hostname not in self.allowed_hosts
            or parts.fragment
        ):
            raise EntraHttpError("URL host is not allowlisted")
        request_headers = {"Accept": "application/json"}
        if headers:
            for name, value in headers.items():
                if (
                    not isinstance(name, str)
                    or not isinstance(value, str)
                    or not name
                    or len(name) > 128
                    or len(value) > 8192
                    or name.lower() in {"host", "transfer-encoding"}
                ):
                    raise EntraHttpError("Invalid request header")
                request_headers[name] = value
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds, context=self.ssl_context
            ) as response:
                status = getattr(response, "status", None)
                if status != 200:
                    raise EntraHttpError("Unexpected status")
                chunks: list[bytes] = []
                total = 0
                while True:
                    piece = response.read(65536)
                    if not piece:
                        break
                    total += len(piece)
                    if total > self.maximum_bytes:
                        raise EntraHttpError("Response too large")
                    chunks.append(piece)
        except EntraHttpError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError, ValueError):
            raise EntraHttpError("Identity provider unavailable") from None
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise EntraHttpError("Invalid JSON response") from None
        if not isinstance(payload, dict):
            raise EntraHttpError("JSON object required")
        return payload
