"""Pure, conservative URL and IPv4 approval rules; no DNS or transport I/O."""

import ipaddress
import re
from urllib.parse import urljoin, urlsplit

from nexusflow.modules.connectors.destination_types import DestinationError
from nexusflow.modules.connectors.types import https_origin

_DENIED_IPV4 = tuple(
    ipaddress.IPv4Network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "168.63.129.16/32",
    )
)


def _url_text(value: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 4096
        or any(ord(char) <= 32 or ord(char) >= 127 for char in value)
        or "\\" in value
        or "#" in value
        or re.search(r"%(?![0-9a-fA-F]{2})", value)
    ):
        raise DestinationError("invalid_request")
    for match in re.finditer(r"%([0-9a-fA-F]{2})", value):
        byte = int(match[1], 16)
        if byte < 32 or byte in {92, 127}:
            raise DestinationError("invalid_request")


def _canonical_url(url: str) -> tuple[str, str, str, int]:
    _url_text(url)
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path.startswith("//"):
            raise ValueError
        origin = https_origin(f"{parsed.scheme}://{parsed.netloc}")
        authority = urlsplit(origin)
        host = authority.hostname
        if host is None:
            raise ValueError
        port = authority.port or 443
        canonical = origin + (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        _url_text(canonical)
        return canonical, origin, host, port
    except ValueError:
        raise DestinationError("invalid_request") from None


def request_url(base_origin: str, path: str) -> str:
    """Combine only an origin-relative path, never a caller-selected authority."""
    _url_text(path)
    if not path.startswith("/") or path.startswith("//"):
        raise DestinationError("invalid_request")
    try:
        origin = https_origin(base_origin)
    except (TypeError, ValueError):
        raise DestinationError("invalid_request") from None
    return _canonical_url(origin + path)[0]


def redirect_url(previous_url: str, location: str) -> str:
    """Validate raw redirect text before URL joining can discard controls."""
    canonical_previous = _canonical_url(previous_url)[0]
    _url_text(location)
    if location.startswith("//"):
        raise DestinationError("invalid_request")
    try:
        raw = urlsplit(location)
        if raw.scheme and (raw.scheme != "https" or not raw.netloc):
            raise DestinationError("invalid_request")
        return _canonical_url(urljoin(canonical_previous, location))[0]
    except ValueError:
        raise DestinationError("invalid_request") from None


def checked_url(url: str, allowed_origins: tuple[str, ...]) -> tuple[str, str, str, int]:
    """Validate exact canonical origin membership before any resolver is called."""
    if type(allowed_origins) is not tuple or not 1 <= len(allowed_origins) <= 50:
        raise DestinationError("unavailable")
    try:
        if any(
            type(origin) is not str or https_origin(origin) != origin for origin in allowed_origins
        ) or len(set(allowed_origins)) != len(allowed_origins):
            raise DestinationError("unavailable")
    except (TypeError, ValueError):
        raise DestinationError("unavailable") from None
    result = _canonical_url(url)
    if result[1] not in allowed_origins:
        raise DestinationError("blocked_destination")
    return result


def checked_ipv4(addresses: tuple[str, ...]) -> tuple[str, ...]:
    """Approve every bounded A answer; never silently filter a forbidden answer."""
    if (
        type(addresses) is not tuple
        or not 1 <= len(addresses) <= 16
        or any(type(value) is not str for value in addresses)
        or len(set(addresses)) != len(addresses)
    ):
        raise DestinationError("unavailable")
    parsed: list[ipaddress.IPv4Address] = []
    for raw in addresses:
        if not 1 <= len(raw) <= 15:
            raise DestinationError("blocked_destination")
        try:
            address = ipaddress.IPv4Address(raw)
        except (ipaddress.AddressValueError, ValueError):
            raise DestinationError("blocked_destination") from None
        if (
            str(address) != raw
            or not address.is_global
            or address.is_private
            or address.is_reserved
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or any(address in network for network in _DENIED_IPV4)
        ):
            raise DestinationError("blocked_destination")
        parsed.append(address)
    return tuple(str(address) for address in sorted(parsed))
