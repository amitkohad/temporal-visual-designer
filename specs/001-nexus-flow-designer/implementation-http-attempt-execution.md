# HTTP attempt execution increment

Branch: `codex/054-http-attempt-execution`, based on main `fae8936` (PR #51).

## Implemented boundary

Compose internal credential-free HTTP requests through fresh scoped registry
authorization, pinned destination preparation, post-DNS revalidation and a
synchronous post-TLS pre-write guard. Reject secret-bearing definitions before
DNS. Do not accept external approval tokens, resolve credentials, retry, follow
redirects or register API/workflow/launcher capabilities.

Freeze contracts and per-lane paths before implementation. Preserve the existing
low-level sender API with an optional guard and share strict request snapshots
between transport and executor. Private provider results are checked for identity,
shape and bounds. Errors have closed codes/fixed text, no automatic retry hint
and conservative delivery uncertainty. Deadlines and cancellation span all
attempt phases, subject to trusted synchronous/provider cooperation.

See [ADR 0052](../../docs/decisions/0052-http-attempt-execution.md) and the
[coordination guide](../../docs/http-attempt-execution.md).

## Parallel plan and acceptance

1. Coordinator freezes contracts/request policy and owns transport guard,
   guard tests, dependency decisions, documentation and final regression.
2. [P] Planner lane owns credential-free preparation/revalidation, immutable
   scope/binding checks, secret-before-DNS and isolated tests.
3. [P] Executor lane owns one-attempt sequencing, deadline/cancellation, provider
   result validation and private failure/ambiguity tests.
4. [P] Independent acceptance lane owns real SQLite/planner/sender/TLS scenarios
   with fake DNS and a test-only asserted numeric-pin loopback dial redirect.
5. Review results and record verified checkboxes; full parent acceptance stays
   open where credentials, retries, providers, workflow or UI are incomplete.

Independent final review found no blockers. The four new suites passed **167
tests** (4.48 seconds): 40 planner, 100 executor, 12 sender guard and 15 independent
real registry/TLS cases. These include secret-before-DNS denial, exact three-gate
ordering, revoked permission/corrupted definitions after TLS preventing HTTP
writes, malformed inputs/results, guard contract failures, privacy, no-follow
redirects, total DNS/dial/read deadlines, cancellation and post-write uncertainty.

Ruff lint/format (480 Python files), mypy (111 source files), locked dependency
checks, schema exports, source/wheel builds and diff checks passed. No dependency
versions changed. The wheel was inspected for all three new production modules.
Full default regression on final source: **2,786 passed, one expected Linux TCP
TIME_WAIT test skipped on Windows, 75 integration tests deselected** (385.96
seconds). Temporal/runtime source is unchanged and its opt-in real integration
suite was not rerun for this internal adapter increment. No public egress, live
Entra, cloud or browser acceptance is claimed.

## Remaining scope

Secret authorization/resolution, credential and payload redirect policy, retry and
durable delivery recovery, project/run/step identity binding, typed descriptors,
Dev mocks, response redaction/mapping, other connectors, public egress/live
identity, Temporal registration, default launcher and Designer/browser integration
remain open. Internal planner/sender ports are trusted; validation cannot undo a
malicious adapter's effects or provide atomic revocation of an external request.
