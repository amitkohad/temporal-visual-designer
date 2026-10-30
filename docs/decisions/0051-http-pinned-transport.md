# ADR 0051: Credential-free pinned HTTPS attempts

Status: accepted for the internal, single-attempt transport subset of feature 08.

## Decision and trust boundary

Add `AsyncIPv4Resolver` and `PinnedHTTPTransport`, behind the existing resolver
and a new `PinnedHTTPSender` port. Freeze `HTTPAttemptRequest`,
`HTTPAttemptResult` and `HTTPTransportError` in `connectors/transport_types.py`
before parallel implementation. These are internal Python contracts, not public
JSON schemas or dispatch APIs. No default launcher or workflow activity is wired.

`ApprovedHTTPDestination` is a trusted internal routing record, not a signed
authorization capability. The transport repeats structural URL, origin, host,
port and public-address validation, but cannot independently establish registry
authorization, provenance or freshness. A future execution coordinator must
perform fresh preflight immediately before each attempt, enforce run/step and
revision binding, and reject credential-bearing connections until an authorized
secret adapter exists. Calling the low-level transport alone does not authorize
a connection or make a configured secret optional.

## Routing and TLS

Resolve A records with asynchronous `loop.getaddrinfo`, constrained to IPv4/TCP.
Deduplicate and bound results; reject the entire answer set if any address fails
the existing public-only policy. The await is bounded, late answers are rejected
and caller cancellation propagates. Underlying system DNS may continue in the
executor after cancellation; the adapter cannot interrupt an OS resolver call.

Dial only the first canonical approved numeric IPv4 address. Do not retry another
address, perform independent hostname resolution, read proxy environment settings
or follow redirects. Preserve the original hostname for TLS SNI/certificate
verification and HTTP Host. Require certificate verification and hostname checks;
never add a production loopback or insecure-TLS switch. Send HTTP/1.1 requests,
accepting HTTP/1.0 or HTTP/1.1 responses; HTTP/2 and upgrades are unsupported.

## HTTP and failure semantics

Use `h11` as a direct runtime dependency for HTTP/1.1 framing. Permit the fixed
GET, HEAD, POST, PUT, PATCH, DELETE and OPTIONS methods with opaque bounded bytes;
GET/HEAD bodies are unsupported. A fixed content-type enum replaces arbitrary
request headers. The transport owns Host, framing, connection-close and identity
content-coding headers. No Authorization, cookies, credentials or custom headers
are accepted; no decompression, pooling or automatic retry occurs.

Bound request bytes, response bytes, cumulative headers, informational responses
and total attempt time. Validate complete framing, including trailers. Cumulative
metadata includes chunk framing as a conservative wire-overhead budget. Stream cleanup
has an additional bounded 0.5-second shutdown window after the attempt.

Return status and raw bounded headers/body privately; a 3xx response is not another
network request. The coordinator must call `prepare_redirect` before a new hop
and separately decide method/body semantics and sensitive-data forwarding.

Mark delivery as potentially having occurred before the first write. A timeout,
malformed response or other failure after that point is not evidence of no remote
effect. Errors contain closed codes and fixed text; all transport errors have
`retryable=False`. Operation identity is retained locally, but no upstream
idempotency guarantee or header is implied. Cancellation propagates and closes
the stream; a cancelled caller must conservatively treat delivery as unknown.

Bodies, headers and URLs can contain credentials or business data even when this
adapter adds no credential fields. Hide them from representations and do not
forward them into logs, exports or debugger inspection. A separate redaction and
response-mapping boundary is required before workflow exposure.

## Verification and remaining gates

Parallel ownership: coordinator owns contracts/dependencies/specification;
resolver lane owns resolver/unit tests; transport lane owns wire/parser/unit
tests; acceptance lane owns independent loopback TLS tests. Test-only dial
redirection can route an asserted public pin to a disposable loopback server;
it must not alter production destination policy. Such tests establish actual
socket/TLS behavior under a controlled dial, not live public DNS/egress, cloud
identity, workflow execution or browser acceptance.

Execution coordination, secret resolution, credential/payload redirect rules,
retry/deduplication/ambiguous-outcome recovery, mock descriptors, email and
notification connectors, durable run binding and UI remain separate gates.

Primary implementation references: [h11 event/framing API](https://h11.readthedocs.io/en/latest/api.html),
[Python stream TLS options](https://docs.python.org/3.12/library/asyncio-stream.html#asyncio.open_connection)
and [async DNS/executor behavior](https://docs.python.org/3.12/library/asyncio-eventloop.html#asyncio.loop.getaddrinfo),
consulted 2026-10-30.
