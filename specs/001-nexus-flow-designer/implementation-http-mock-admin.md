# HTTP JSON mock fixture administration increment

Branch: `codex/059-http-mock-admin`, based on main `fce0af2` (PR #56).

## Scope and parallel plan

Expose signed Dev-only immutable fixture writes and metadata reads using an
explicit host profile and the actual persistent fixture store. Preserve default
hosts and existing worker execution permissions.

1. Coordinator freezes JSON write/metadata/store/error interfaces.
2. [P] Service lane owns strict snapshots, administrator/Dev policy, immutable
   provider mapping, private errors and independent service tests.
3. [P] HTTP lane owns optional backend composition, exact routes and signed tests.
4. [P] Acceptance lane composes signed API, reopened SQLite fixtures and actual
   mock policy/executor/activity with network forbidden.
5. Coordinator reviews evidence, updates verified task checkboxes and runs checks.

See [ADR 0057](../../docs/decisions/0057-http-mock-admin.md) and the
[API guide](../../docs/http-mock-admin.md).

## Verification

Focused verification passed: **64 service tests** (1.01 seconds), **27 signed HTTP
tests** (18.07 seconds), and **seven independent acceptance tests** (7.06 seconds).
The 98 new cases verify strict/canonical JSON, immutable retries/conflicts/quota
handling, malformed provider receipts/scenarios, scope/config enablement,
authorization before provider access, revocation after lookup, identity error
classification, query rejection, metadata/error privacy and cancellation.

Independent acceptance composes actual signed local HTTP, SQLite reopen,
`ExplicitDevHTTPMockPolicy`, `HTTPMockExecutor` and `HTTPJSONActivityAdapter` with
forbidden network/live sentinels. Tokens/identity and worker grants are synthetic;
these results do not establish deployed Entra, browser or Temporal acceptance.
Independent service/HTTP review passed after an invariant non-Dev denial was
placed before host-profile inspection.

Ruff lint/format, configured mypy (126 source files), lock verification and
source/wheel build passed. The wheel matches all four final service/contract/HTTP
modules. Full default regression passed **3,472 tests**, with one expected Windows
skip and 81 integration tests deselected (409.10 seconds). No dependencies,
workflow serialization or default launcher composition changed. No new Temporal
acceptance is inferred from the signed local API/executor composition.

## Remaining scope

This subset has no fixture body download, list/history/delete, binary/header
authoring, browser UI, fixture packaging or default launcher registration. Worker
execution permission, executable project publication, live HTTP effect recovery,
persistent-fixture Temporal restart and cloud/production identity acceptance are
separate concerns.
