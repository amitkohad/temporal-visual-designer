# Internal pinned HTTPS transport

The HTTP transport is a low-level adapter for credential-free, single-attempt
HTTPS. It is not enabled by the local launcher or exposed as an API/workflow
activity. A trusted execution coordinator must authorize and prepare a fresh
destination immediately before sending; a constructed plan is not proof of
authorization. Credential-bearing connections must wait for the secret-aware
execution boundary rather than silently discarding their configured credentials.

The [credential-free attempt coordinator](http-attempt-execution.md) now supplies
fresh authorization before DNS, after DNS and after TLS before the first HTTP
write. It uses `send(..., before_write=guard)`, an optional synchronous hook that
must return `None` or raise a closed transport error before any request bytes.
No hook is required for standalone trusted low-level callers; that API alone is
not an authorization boundary. Known guard denials preserve their safe error
codes and report no HTTP transmission.

`AsyncIPv4Resolver` implements the preflight resolver port with asynchronous
IPv4/TCP system lookup. It applies the existing all-answer public-address policy,
bounded result size and timeout. System DNS can outlive a cancelled await in an
executor; late results are not approved.

`PinnedHTTPTransport.send(destination, request)` connects only to the first
approved numeric IP and uses the original hostname for verified TLS and Host.
It makes one attempt: no address fallback, proxy, automatic redirect, automatic
retry, decompression or connection pool. TLS contexts must verify certificates
and hostnames. Request methods and content types are fixed; there is no arbitrary
header or credential interface. GET and HEAD carry no request body.

| Limit | Default | Configuration boundary |
| --- | --- | --- |
| Attempt timeout | 10 seconds | Greater than zero, at most 60 seconds |
| Request / response body | 1 MiB each | 0–4 MiB each |
| Cumulative response metadata | 16 KiB | 1–64 KiB; includes interim/trailer headers and chunk framing |
| Response header count | 100 | Fixed cumulative cap |
| Informational responses | 5 | Fixed; protocol upgrades are unsupported |
| Stream shutdown | 0.5 seconds | Additional bounded cleanup after the attempt |

Only HTTP/1.0 and HTTP/1.1 responses are accepted. The public numeric-address
policy is checked again before dialing. There is no fallback to a second approved
address when the first connection fails.

The result contains the stable operation ID, status, bounded raw headers and body.
Headers and bodies are hidden from representations. They are still sensitive raw
provider data: do not serialize them into logs, exports or debug inspection.
A 3xx response does not trigger another request. A future coordinator must use
`prepare_redirect` and separately decide whether method/body forwarding is safe.

All errors have fixed text and closed codes. `retryable` is always false;
`delivery_may_have_occurred` becomes true before writing, so a timeout or malformed
response cannot be treated as proof that a remote effect did not happen.
Cancellation propagates and closes the stream; delivery must be considered
unknown after a cancelled attempt. The operation ID is retained locally and does
not establish remote deduplication.

Tests use fake DNS/streams and disposable local TLS servers. Local TLS acceptance
redirects an asserted public numeric dial to loopback only inside tests, without
changing production address policy. Public DNS/egress, deployment identity and
workflow/UI acceptance require separate verification. See
[ADR 0051](decisions/0051-http-pinned-transport.md) for the full decision.
