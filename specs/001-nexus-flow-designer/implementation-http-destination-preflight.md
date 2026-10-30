# HTTP destination preflight increment

Branch: `codex/052-http-destination-preflight`, based on main `50f95ad`
(merged PR #49).

## Implemented boundary

Add internal worker routing plans backed by the immutable connection registry.
Each initial request and redirect performs fresh `worker.execute` authorization
before private lookup/DNS, validates scope/revision/digest and reference-presence
metadata, and checks exact allowed HTTPS origin before an injected resolver runs.
Malformed actor results fail closed. No browser/API or default-worker dispatch
entry point is added.

Initial paths are origin-relative; redirect URLs are validated before joining.
Malformed escapes, raw/encoded controls, backslashes, credentials, fragments,
authority overrides and ambiguous/scheme-relative redirects are rejected. All
bounded DNS A answers must be canonical public IPv4, with conservative explicit
special-use and Azure-platform exclusions. Mixed answer sets fail rather than
silently filtering. IPv6/private targets/proxies are unsupported.

Approved immutable plans retain selected revision/digest, stable operation ID,
original TLS host/port, URL and exact approved addresses. Redirects validate prior
binding, reauthorize/re-resolve and enforce the hop budget. No HTTP bodies,
headers or credential references are carried in plans or resolved. Query values
remain private and must not be logged. URL/path representations are hidden, provider
errors are normalized and only unavailable outcomes are potentially retryable.
No retries or external effects occur here.

DNS awaits are bounded and cooperative cancellation propagates. A post-resolver
monotonic check prevents approval of late answers even when a provider suppresses
cancellation or blocks the timeout callback. This does not preempt arbitrary
synchronous code; actual resolver/transport adapters remain gated.

See [ADR 0050](../../docs/decisions/0050-http-destination-preflight.md) and
[worker preflight guide](../../docs/http-destination-preflight.md). Future HTTP
transport must honor address pins/TLS identity and disable independent DNS,
proxy and automatic redirect fallbacks. Allowlisted destinations do not grant
cross-origin credential forwarding.

## Parallel ownership and verification

Shared contracts preceded planner, pure URL/IP policy and independent real-registry
acceptance lanes. The coordinator owns contracts, docs, checklists and regression.
Independent review identified and verified malformed-actor rejection; final review
added late-answer rejection when the timeout callback was delayed. No blocking
findings remain.

Final focused acceptance: **173 passed** (2.79 seconds): 39 planner, 97 pure policy
and 37 independent real-registry cases. Tests cover authorization-before-access,
cross-scope/reference/digest failures, redirects and rebinding, special-use address
boundaries, representation/error privacy, timeouts, cancellation and zero actual
DNS/socket/HTTP/secret-provider activity.

Ruff lint and formatting (460 Python files), mypy (105 source files), source/wheel
build and `git diff --check` passed. Full default regression on final source:
**2,437 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 75
integration tests deselected** (400.35 seconds). Runtime/Temporal code is unchanged
and its real integration suite was not rerun for this isolated routing increment.
No actual transport, live Entra, cloud or browser acceptance is claimed.

## Remaining scope

Real DNS/HTTP transport, pin/TLS enforcement, secret authorization/resolution,
payload/response limits, delivery retry/idempotency/ambiguity policy, descriptors,
mocks, project/run/step bindings, worker/Designer integration, private/IPv6 policy
and full F08 acceptance remain open.
