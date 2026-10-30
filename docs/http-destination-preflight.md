# Prepare an HTTP destination inside a worker

`HTTPDestinationPlanner` consumes an explicit connection repository, worker
authorization provider and asynchronous IPv4 resolver. It is an internal routing
boundary; it is not exposed through an execution API or enabled in the default
launcher.

```python
request = HTTPDestinationRequest(
    connection_id="hr-api",
    connection_revision="v1",
    operation_id="run-step-1",
    path="/employees?team=engineering",
)
plan = await planner.prepare(scope, request)
redirect = await planner.prepare_redirect(scope, request, plan, "/next-page")
```

Every hop checks worker permission before reading the pinned private definition
or resolving a host. Exact canonical origin membership is checked before DNS.
Each resolver answer must be an approved public IPv4 address; a mixed answer set
fails closed. DNS is limited to 16 unique canonical answers and a bounded await.
No secrets are resolved or passed to the resolver.

The result retains connection revision/digest and operation identity, along with
approved addresses and TLS host/port for a downstream transport. Its full URL is
excluded from `repr`; do not serialize internal plans into logs, packages or
debug output. Errors have a fixed message/code and retryability hint. A provider
failure does not expose its exception text. No HTTP request or delivery retry
occurs in preflight.

Redirect approval validates the prior plan's binding, reauthorizes and resolves
the new target. The default limit is five redirects. Absolute HTTPS and relative
redirects are supported; scheme-relative redirects, IPv6/private targets,
userinfo credentials in URLs and origin escapes are rejected. Query values may
contain business data and are not a general secret-detection boundary. New registry revisions do
not change a selected immutable revision.

The [internal resolver and pinned HTTPS adapter](http-pinned-transport.md) now
provide system A-record lookup and credential-free single attempts with verified
TLS and response limits. A plan is not an unforgeable capability: fresh preflight
must precede every attempt under a trusted coordinator. Secret handling,
idempotency, redirect method/body rules and workflow wiring remain open. See
[ADR 0050](decisions/0050-http-destination-preflight.md) for preflight decisions.
