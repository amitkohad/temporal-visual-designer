# Durable project-package intake increment

Branch: `codex/035-project-package-intake`, based on main `fbb71e7` (merged PR #32).

## Implemented boundary

Authorized users can persist an inspected package as an immutable target-environment record with state `staged`. The record includes a scope-local revision, source project/release/revision identity, logical package digest, verified uploader and timestamp. First archive bytes, manifest, physical checksum/size and metadata commit atomically. Exact/repacked logical retries retain the original actor/receipt/archive; changed content under the same operation ID conflicts. The default scope quota is 100 records and exact retries are checked before quota.

All routes require fresh `package.import`. Stage reuses bounded decoding and same-organization declaration checks. Metadata history is bounded and does not select archives/manifests; manifest and download reads verify identity/integrity. Staging never writes project drafts, published artifacts, releases, active pointers or execution state. Self-declared organization metadata and checksums are not provenance or executable trust.

Generic hosts configure an optional import repository explicitly; permission is checked before reporting unavailable storage. The local demo owns persistent `package-imports.db` with existing ExitStack lifecycle. It is additive and not a mandatory initialization marker, so existing installations require no reinitialization. Preserve it during backup/recovery. Existing codec and HTTP upload budgets remain unchanged.

See [ADR 0033](../../docs/decisions/0033-project-package-intake.md), [API contracts](../../docs/api.md) and the [walkthrough](../../docs/package-import-staging.md). No dependencies, role grants or runtime commands change. The new dedicated SQLite table stores staged packages separately from target executable state.

## Parallel ownership and verification

The domain lane owned `package_promotion/package_imports.py` and `tests/package_promotion/test_package_imports.py`. The API/bootstrap lane owned `apps/api.py`, `api_backend.py`, `local_bootstrap.py`, `tests/api/test_package_imports.py` and `tests/local/test_package_import_bootstrap.py`. Independent acceptance owned `tests/api/test_package_import_acceptance.py` and `tests/runtime/test_local_package_import_integration.py`. Interfaces and ownership were frozen before parallel work; the coordinator owns documentation and final verification.

Focused domain tests: 31 passed. New API/bootstrap tests: 10 passed. Broader API/local regression: 340 passed, one expected Windows platform skip (125.84 seconds). Independent signed HTTP/SQLite acceptance: 10 passed. Tests cover logical retry and first-byte preservation, changed-operation conflicts, lost acknowledgments, quota races, scope-local sequences/cursors, closed/corrupt stores, metadata-only reads, cross-organization refusal, fresh revocation, optional-provider authorization order, UAT/Prod roles and additive local lifecycle.

Independent domain/API reviews found no actionable defects after cleanup/invariant corrections. Ruff lint/format and full mypy (79 source files) passed; source distribution and wheel built. The final default suite passed: 1,365 passed, one Linux-only TCP TIME_WAIT test skipped on Windows, and 42 integration tests deselected (184.43 seconds). The full actual Temporal runtime suite passed: 42 passed, 198 deselected (253.61 seconds), using `.venv/Scripts/python.exe -m pytest -m integration nexusflow/tests/runtime -q`. The new CLI/socket/SQLite/Temporal case verifies Dev export to UAT staging, no UAT project/activation, first archive/receipt after restart/repack retry, unchanged Dev activation/execution, and scoped access revocation.

## Remaining scope

F09-T01f/T02f/T04f/T05g and NF-T013f are checked at this staging boundary. Executable target release/artifact materialization, provenance/trust policy, configuration/worker readiness, activation of staged packages, production providers, retention/deletion and Admin UI remain outstanding. Parent promotion tasks remain open. A staged operation ID is not a release ID. No hosted CI, browser, live Entra or complete target-deployment acceptance is claimed.
