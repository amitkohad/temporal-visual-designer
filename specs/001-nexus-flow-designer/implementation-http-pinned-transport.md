# HTTP pinned transport increment

Branch: `codex/053-http-pinned-transport`, based on main `bbc2a88` (PR #50).

## Scope and frozen contracts

Implement credential-free, single-hop HTTPS transport and the actual asynchronous
IPv4 resolver following [ADR 0051](../../docs/decisions/0051-http-pinned-transport.md).
Use the existing scoped preflight contracts and new frozen attempt/result/error
types. The transport is an internal adapter; authorization/freshness require an
upstream coordinator and are not conferred by constructing a routing record.
No new public endpoint, default launcher registration or workflow dispatch.

## Parallel plan

1. Freeze shared contracts, dependency selection, limits and trust boundaries.
2. [P] Resolver lane: asynchronous IPv4/TCP lookup, strict all-answer policy,
   deadline/cancellation behavior and isolated provider tests.
3. [P] Transport lane: numeric-IP dial, verified original TLS hostname, h11
   framing, bounded bodies/headers/deadline, cleanup and isolated stream tests.
4. [P] Independent acceptance lane: disposable CA/server, actual loopback TLS
   under test-only asserted dial redirection, SNI/Host, certificate failures,
   framing, cancellation/timeout, privacy and redirect non-following.
5. Coordinator: integration review, focused/full regression, lint/type/build and
   evidence-backed checkboxes. No concurrent dependency/environment mutation.

Each lane has disjoint production/test files. Resolver and transport consume
frozen interfaces and can be tested independently without Temporal, Designer,
Azure or public internet. Test-only loopback routing must not add a production
policy bypass.

## Acceptance and evidence

Independent final review found no implementation blockers. The final focused
resolver/transport/TLS command passed **182 tests** (5.72 seconds): 78 resolver,
87 transport and 17 independent acceptance cases. Provider fakes cover malformed
answers, bounds, late/suppressed cancellation, parser/framing/metadata limits and
privacy; actual controlled TLS sockets cover certificate/hostname rejection,
SNI/Host preservation, numeric-only dialing, redirect non-following, framing,
timeout and cancellation cleanup. No public network or actual system DNS lookup
was used. The test-only dial redirect preserves production public-IP policy.

Ruff lint and formatting (470 Python files), mypy (108 source files), all schema
exports, source/wheel builds, locked dependency checks and diff checks passed.
The wheel was inspected for all three new production modules and the h11 runtime
dependency. Full default regression on final source: **2,619 passed, one expected
Linux TCP TIME_WAIT test skipped on Windows, 75 integration tests deselected**
(374.02 seconds). No runtime/Temporal source changed, so the real Temporal suite
was not rerun for this adapter increment. No public egress, cloud, live Entra or
browser acceptance is claimed.

## Remaining scope

Fresh-authorized attempt orchestration, real public DNS/egress acceptance,
secret resolution, retry/idempotency, redirect method/body and sensitive-data
policy, run/step binding, response redaction/mapping, other connectors, mock
descriptors, workflow/Designer wiring and browser/cloud acceptance remain open.
