# HTTP mock fixture metadata discovery increment

Branch: `codex/060-http-mock-listing`, based on main `7eb997c` (PR #57).

## Scope and parallel plan

Allow explicitly enabled Dev administrators to discover saved scoped fixture
revisions and digest pins without loading private fixture bodies for listing.

1. Coordinator freezes private receipt-page/listing and public metadata-page types.
2. [P] Repository lane owns bounded metadata queries, lexical keyset cursors,
   private corruption handling and independent SQLite tests.
3. [P] Service/HTTP lane owns fresh authorization, provider-page validation,
   signed route/query parsing and service/API tests.
4. [P] Acceptance lane owns signed write/list/reopen/continue/select/execute
   composition, isolation/privacy and metadata-versus-content integrity checks.
5. Coordinator owns docs, verified checkboxes, final review and regression.

See [ADR 0058](../../docs/decisions/0058-http-mock-listing.md) and the
[discovery guide](../../docs/http-mock-listing.md).

## Verification

Focused verification passed: **38 new repository tests** (1.50 seconds), **40
service tests** (0.45 seconds), **22 signed API tests**, and **13 independent
acceptance tests** (11.54 seconds). Existing repository/service tests also passed
alongside the new suites. The 113 new cases verify lexical ordering, paired
cursors, bounds/lookahead, SQL metadata guards, no private-document materialization,
scope isolation, provider page/error validation and pre/post authorization.

Independent acceptance composes signed local writes/discovery, reopened SQLite,
exclusive continuation and a selected verified pin with the actual mock
policy/executor/activity adapter. It also verifies inserts before a cursor are not
backfilled and same-sized private-content corruption can be listed but is rejected
by verified reads/execution. Tokens and worker grants are synthetic; network/live
fallback sentinels are forbidden. No browser, deployed Entra or Temporal acceptance
is inferred.

Independent review found no blockers. Coordinator review additionally hardened
malformed provider error-code comparison and added a regression case. Ruff
lint/format, configured mypy (127 source files), lock verification and source/wheel
build passed. Wheel parity passed for all five final listing modules. No
dependency, workflow serialization or default host changes were made.

The initial full run passed 3,583 cases and exposed two existing 10-millisecond
deadline tests whose setup could expire before the intended provider phase.
Test-only fixes arm the real timeout after that phase enters, preserving actual
cancellation, suppressed-result rejection, delivery uncertainty and cleanup.
Both fixes passed independent review; the surrounding execution and mock
acceptance files passed 100 and 19 tests respectively. Production timeout behavior
was not changed.

Final full default regression: **3,585 passed**, one expected Windows skip and
81 integration tests deselected (445.03 seconds). Final Ruff lint/format and
diff checks also passed.

## Remaining scope

Listing is not content verification, execution readiness, a frozen export view
or complete connector management. Browser UI, fixture body downloads,
history/delete, package transfer, default host registration and persistent-fixture
Temporal/cloud acceptance remain open.
