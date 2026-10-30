# Coordinate a credential-free HTTP attempt

`HTTPAttemptExecutor` connects the existing connection registry, destination
planner and pinned HTTPS sender through explicit internal ports. Hosts must
configure these providers; no HTTP execution route, default worker registration
or launcher capability is added.

```python
planner = HTTPDestinationPlanner(repository, worker_authorization, AsyncIPv4Resolver())
executor = HTTPAttemptExecutor(planner, PinnedHTTPTransport())
result = await executor.execute(
    scope,
    HTTPDestinationRequest(
        connection_id="hr-api",
        connection_revision="v1",
        operation_id="run-step-1",
        path="/employees",
    ),
    HTTPAttemptRequest(method="POST", body=b'{"employee":"example"}'),
)
```

This makes one attempt. The registry revision must have no secret reference;
otherwise execution fails with `credentials_required` before DNS or HTTP. Secret
resolution and authenticated connectors are separate work. No automatic retry or
deduplication is implied by the operation ID.

Authorization and immutable connection integrity are checked before DNS, after
DNS before sender entry, and again after TLS immediately before the first HTTP
write. Revocation or corrupted bindings observed at any check fail closed.
Authorization is not atomic with an external network effect after the final
decision. Custom senders must invoke the synchronous `before_write` guard exactly
once before any HTTP bytes and propagate its denial without writing.

The default outer deadline is 30 seconds (configurable up to 120). Request and
response byte caps default to 1 MiB (0–4 MiB supported); header validation defaults
to 16 KiB (1–64 KiB). The actual transport also enforces its own limits and
cumulative wire-framing budget; configure both deliberately. Cooperative
cancellation propagates. Synchronous provider blocking and background OS DNS
cannot be forcibly interrupted by an asyncio deadline.

Results contain private bounded raw status/headers/body and the operation ID.
They are not safe export/debug/log payloads. A 3xx response is returned without
following it; redirect request semantics and sensitive-data forwarding remain
unsupported. Durable workflow/run/step binding and response mapping are not
provided by this internal call.

Errors expose fixed text, closed codes, `retryable=False` and conservative
`delivery_may_have_occurred`. A known TLS/guard failure before writing reports
false; unknown sender errors, post-write failures, invalid responses or an outer
timeout during send may report true. Cancellation during send also leaves
delivery uncertain. Retrying automatically could duplicate an effect.

See [ADR 0052](decisions/0052-http-attempt-execution.md) and
[pinned transport](http-pinned-transport.md). Independent acceptance uses real
SQLite and disposable TLS sockets with fake DNS and test-only public-pin dial
redirection. It makes no public network, live Entra, cloud or browser claim.
