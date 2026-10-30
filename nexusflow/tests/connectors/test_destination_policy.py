"""Pure policy boundaries never touch DNS, sockets, credentials or payloads."""

import ipaddress

import pytest

from nexusflow.modules.connectors.destination_types import DestinationError
from nexusflow.modules.connectors.policy import (
    checked_ipv4,
    checked_url,
    redirect_url,
    request_url,
)

ALLOWED = ("https://api.example.com", "https://other.example.com:8443")


def assert_error(code, operation):
    with pytest.raises(DestinationError) as error:
        operation()
    assert error.value.code == code
    assert str(error.value) == "HTTP destination preflight failed"
    assert error.value.retryable is (code == "unavailable")


def test_request_canonicalizes_origin_and_preserves_encoded_path_and_query():
    value = request_url(
        "HTTPS://API.EXAMPLE.COM:443/", "/employees/a%2Fb?q=hello%20world&x=%E2%82%AC"
    )
    assert value == "https://api.example.com/employees/a%2Fb?q=hello%20world&x=%E2%82%AC"
    assert checked_url(value, ALLOWED) == (value, ALLOWED[0], "api.example.com", 443)
    assert request_url(ALLOWED[0], "/") == "https://api.example.com/"


@pytest.mark.parametrize(
    "path",
    ["", "employees", "https://evil.example/x", "//evil.example/x", "///evil.example", "?q=1"],
)
def test_request_cannot_replace_base_authority(path):
    assert_error("invalid_request", lambda: request_url(ALLOWED[0], path))


@pytest.mark.parametrize(
    "value",
    [
        "http://api.example.com/x",
        "https://user:never-secret@api.example.com/x",
        "https://@api.example.com/x",
        "https://api.example.com/x#fragment",
        "https://api.example.com/x#",
        " https://api.example.com/x",
        "https://api.example.com/has space",
        "https://api.example.com/x\n",
        "https://api.example.com/x\r\nInjected:secret",
        "https://api.example.com\\@evil.example/",
        "https://api.example.com/%",
        "https://api.example.com/%2",
        "https://api.example.com/%zz",
        "https://api.example.com/%00",
        "https://api.example.com/%0a",
        "https://api.example.com/%0D%0Aheader",
        "https://api.example.com/%5c",
        "https://api.example.com/%7f",
        "https://api.example.com/é",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://api%2eexample.com/x",
        "https://api.example.com.evil.example@evil.example/x",
        "https://api.example.com:/x",
        "https://api.example.com:65536/x",
        "https://api.example.com:never-secret/x",
        "https://api.example.com//evil.example/x",
    ],
)
def test_invalid_urls_have_no_sensitive_error_details(value):
    assert_error("invalid_request", lambda: checked_url(value, ALLOWED))


def test_url_full_length_limit_includes_origin():
    prefix = "https://api.example.com"
    largest = prefix + "/" + "a" * (4096 - len(prefix) - 1)
    assert len(checked_url(largest, ALLOWED)[0]) == 4096
    assert_error("invalid_request", lambda: checked_url(largest + "a", ALLOWED))
    assert_error("invalid_request", lambda: request_url(prefix, "/" + "a" * 4096))


@pytest.mark.parametrize(
    "value",
    [
        "https://api.example.com.evil.example/x",
        "https://evil.example.com/x",
        "https://api.example.com:8443/x",
        "https://other.example.com/x",
    ],
)
def test_origin_allowlist_is_exact_including_port(value):
    assert_error("blocked_destination", lambda: checked_url(value, ALLOWED))


@pytest.mark.parametrize(
    "allowed", [[], (), ("https://api.example.com/",), (ALLOWED[0], ALLOWED[0]), (42,)]
)
def test_invalid_trusted_allowlist_is_unavailable(allowed):
    assert_error("unavailable", lambda: checked_url("https://api.example.com/x", allowed))


def test_relative_and_absolute_redirects_are_checked_against_allowlist():
    previous = "https://api.example.com/a/b?old=1"
    assert redirect_url(previous, "../c?q=2") == "https://api.example.com/c?q=2"
    assert redirect_url(previous, "?fresh=3") == "https://api.example.com/a/b?fresh=3"
    target = redirect_url(previous, "HTTPS://OTHER.EXAMPLE.COM:8443/resource")
    assert checked_url(target, ALLOWED) == (
        "https://other.example.com:8443/resource",
        ALLOWED[1],
        "other.example.com",
        8443,
    )
    forbidden = redirect_url(previous, "https://evil.example.com/")
    assert_error("blocked_destination", lambda: checked_url(forbidden, ALLOWED))


@pytest.mark.parametrize(
    "location",
    [
        "",
        "//api.example.com/",
        "\\evil.example/x",
        " /new",
        "/new\r\nheader",
        "/new%0d%0aheader",
        "/new%5Cevil",
        "/new#",
        "https://user:secret@api.example.com/",
        "http://api.example.com/",
        "https:evil.example/path",
        "https:/evil.example/path",
        "%zz",
        "javascript:alert(1)",
    ],
)
def test_redirect_raw_validation_precedes_lossy_urljoin(location):
    assert_error("invalid_request", lambda: redirect_url("https://api.example.com/old", location))


def test_public_ipv4_answers_are_canonical_and_sorted_numerically():
    assert checked_ipv4(("11.0.0.1", "9.9.9.9", "1.1.1.1", "8.8.8.8")) == (
        "1.1.1.1",
        "8.8.8.8",
        "9.9.9.9",
        "11.0.0.1",
    )


@pytest.mark.parametrize(
    "network",
    [
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
    ],
)
def test_special_ranges_and_boundaries_are_denied_even_in_mixed_answers(network):
    value = ipaddress.IPv4Network(network)
    for address in (str(value.network_address), str(value.broadcast_address)):
        assert_error("blocked_destination", lambda address=address: checked_ipv4((address,)))
        assert_error(
            "blocked_destination", lambda address=address: checked_ipv4(("8.8.8.8", address))
        )


@pytest.mark.parametrize(
    "value",
    [
        "169.254.169.254",
        "100.100.100.200",
        "192.0.0.9",
        "192.0.0.10",
        "::1",
        "2606:4700:4700::1111",
        "::ffff:8.8.8.8",
        "127.1",
        "2130706433",
        "0x7f000001",
        "0177.0.0.1",
        "008.008.008.008",
        "8.8.8.8 ",
        "",
        "not-an-address",
    ],
)
def test_noncanonical_or_unsupported_address_forms_are_denied(value):
    assert_error("blocked_destination", lambda: checked_ipv4((value,)))


@pytest.mark.parametrize("answers", [[], (), ("8.8.8.8",) * 2, (1,), ("8.8.8.8",) * 17])
def test_malformed_dns_answer_structures_are_unavailable(answers):
    assert_error("unavailable", lambda: checked_ipv4(answers))


def test_pure_policy_never_invokes_network_or_dns(monkeypatch):
    import socket

    def fail(*args, **kwargs):
        raise AssertionError("No network access is permitted")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    target = request_url(ALLOWED[0], "/business?q=opaque")
    assert checked_url(target, ALLOWED)[2] == "api.example.com"
    assert checked_ipv4(("8.8.8.8",)) == ("8.8.8.8",)
