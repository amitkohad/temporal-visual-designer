# Deterministic HTTP mocks in Dev

The mock adapter is a separate internal execution path. A host must explicitly
select `HTTPMockExecutor`, supply a scoped fixture catalog and configure a trusted
Dev authorization policy. The live executor never falls back to fixtures, and no
API, Temporal activity or default launcher capability is added.

`ExplicitDevHTTPMockPolicy(worker_authorization, dev_scopes)` combines a trusted
host-configured list of Dev scopes with fresh `worker.execute` permission. An
environment named `dev` is not enough; unknown scopes and UAT/Prod grants are
denied before reading fixtures. The adapter does not discover deployment kind
from Entra or existing routing records. Configure classification deliberately;
dynamic hosts may implement the `HTTPMockPolicy` port instead.

Construct an `HTTPMockScenario` with scope, connection ID/revision, expected
relative path, `HTTPAttemptRequest`, response status, private headers and bytes.
`StaticHTTPMockScenarios((scenario,))` validates and snapshots fixtures, rejects
duplicate scoped revisions and returns new copies. Its default limits are 100
scenarios and 8 MiB of canonical encoded fixture content, configurable up to 1,000
scenarios and 64 MiB. Fixture and executor request/response/header bounds apply
separately.

Compute the selected pin with `scenario_digest(scenario)` and pass it explicitly:

```python
result = await mock_executor.execute(
    scope,
    destination_request,
    HTTPAttemptRequest(method="GET"),
    fixture_digest=scenario_digest(scenario),
)
```

Exact method, path, content type and body must match. The fixture's scope,
connection revision and digest must also match. Changed fixtures cannot satisfy
an old digest pin. Each result echoes the supplied operation ID and returns the
same private status/headers/body. Missing fixtures or mismatches fail; no live
connection, DNS, HTTP, socket or secret lookup occurs. Permission is rechecked
after fixture lookup so revocation or loss of Dev eligibility cannot release a
response based on an earlier grant.

The static catalog is in-memory host configuration. Explicit hosts can instead
use [persistent private fixtures](persistent-http-mocks.md) to preserve immutable
scoped revisions and pins across restarts. Project packaging and admin fixture
management remain separate work. Repeated
and concurrent mock calls are deterministic, but no effect journal, remote
deduplication or retry scenario is implied.

All mock errors have fixed text, closed codes, no automatic retry and no possible
external delivery. Cancellation propagates, and late provider answers fail under
the bounded outer deadline. Raw fixture/request/response data remain private;
use synthetic data and do not serialize them to logs, exports or debugging UI.
See [ADR 0053](decisions/0053-dev-http-mocks.md) for trust and integration limits.
