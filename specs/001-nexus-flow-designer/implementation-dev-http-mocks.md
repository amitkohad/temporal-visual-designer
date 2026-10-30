# Dev HTTP mock increment

Branch: `codex/055-dev-http-mocks`, based on main `5a3efa0` (PR #52).

## Implemented subset and parallel plan

Advance F08-FR06 with explicitly selected deterministic HTTP mocks. Freeze scoped
private scenario/grant/provider/error contracts before parallel implementation.
Never infer Dev eligibility from an environment name or silently fall back from
live execution to mocks. Require fresh trusted Dev policy and worker permission,
immutable fixture snapshots, explicit digest selection and exact request matching.

1. Coordinator owns contracts, trusted explicit Dev scope policy, policy tests,
   documentation, checkboxes and final regression.
2. [P] Fixture lane owns strict snapshots, canonical digests, bounded immutable
   catalog and isolated validation/mutation/scoping tests.
3. [P] Executor lane owns matching, fresh grants, private responses/errors,
   deadline/cancellation and fake-port tests.
4. [P] Independent acceptance lane composes actual static catalog/explicit policy
   with fake worker grants and asserts no DNS/socket/live fallback/secret access.
5. Review and record verified results; retain incomplete feature parents.

See [ADR 0053](../../docs/decisions/0053-dev-http-mocks.md) and the
[mock guide](../../docs/dev-http-mocks.md).

## Verification

Independent final cross-review found no blockers and reverified all **171 tests**
(1.23 seconds). The initial combined run passed in 1.09 seconds: 17 explicit policy,
78 fixture/catalog, 57 executor and 19 independent composed acceptance tests.
They cover scoped/digest mismatch, immutable snapshot isolation, exact matching,
deterministic repeat/reconstruction/concurrency, non-Dev/malformed grant denial,
fresh revocation, private errors/results, deadline/cancellation and forbidden
DNS/socket/live-executor access. Actual static policy/catalog/executor components
are composed with fake worker authorization; no real identity or live environment
classification is verified.

Ruff lint/format (491 Python files), mypy (115 source files), schema exports,
locked dependency checks and source/wheel builds passed. No dependency versions
changed. The wheel was inspected for all four new production mock modules.
Full default regression on final source: **2,957 passed, one expected Linux TCP
TIME_WAIT test skipped on Windows, 75 integration tests deselected** (384.34
seconds). No runtime/Temporal source changed, so its opt-in integration suite was
not rerun for this mock-only internal increment. No live identity, environment
classification, external provider, persistence, cloud or browser acceptance is claimed.

## Remaining work

The fixture catalog is explicit in-memory host configuration. Persistent fixture
history/storage, package export/import, admin/UI management and authoritative live
environment classification are not implemented. No default API/launcher/Temporal
registration, live Entra, browser or cloud acceptance is claimed. HTTP descriptor
publication, other connector types, stateful retry fixtures, durable effect
recovery, secret handling and workflow/Designer integration remain open.
