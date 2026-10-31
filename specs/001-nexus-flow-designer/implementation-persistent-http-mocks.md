# Persistent HTTP mock fixture increment

Branch: `codex/058-persistent-http-mocks`, based on main `4776a12` (PR #55).

## Scope and parallel plan

Provide a private immutable SQLite implementation of the existing HTTP mock
scenario port. Preserve static fixture identity across restart and compose the
actual executor/activity adapter without enabling live HTTP or default hosting.

1. Freeze receipt/error and codec/repository interfaces.
2. [P] Codec lane: strict canonical bounded document round trips and rejection tests.
3. [P] Repository lane: immutable scoped keys, atomic quotas/retries, integrity,
   concurrent writers, asynchronous reads and restart tests.
4. [P] Acceptance lane: actual persistent fixtures, Dev policy, executor and activity
   composition with forbidden network sentinels and private denial checks.
5. Coordinator: architecture, evidence, task checkboxes, review and regression.

See [ADR 0056](../../docs/decisions/0056-persistent-http-mocks.md) and the
[host guide](../../docs/persistent-http-mocks.md).

## Verification

All **1,219 connector tests passed** (27.61 seconds), including 121 new cases:
62 codec, 42 repository and 17 independent composed acceptance cases. These cover
strict canonical/base64/bounds rejection, static digest parity, reopen recovery,
scope isolation, exact retries and conflicts, independent-instance quota races,
mutation isolation, invalid/oversized/blob stored values, private errors, fresh
Dev eligibility/worker revocation and metadata-only activity output without
network fallback. Tests use synthetic data and local SQLite, not live Entra.

Independent cross-review found no blockers after digest/document type and size
checks were moved ahead of stored-data materialization. Ruff lint/format and
configured mypy (124 source files) passed. Lock verification and source/wheel
build passed; the wheel matches all three final storage modules. No dependency,
workflow serialization, default host or public API changes were made.

The existing focused Temporal mock regression passed **six tests** (14.83 seconds).
It uses static fixtures and does not establish persistent-fixture Temporal
restart acceptance. Full default regression passed **3,374 tests**, with one
expected Windows skip and 81 integration tests deselected (759.81 seconds).

## Remaining scope

Trusted-host writes do not provide public authorization or an admin API. Default
launcher/API/UI, project fixture packaging, executable built-in publication,
live-effect recovery, secret handling and cloud/production acceptance remain open.
