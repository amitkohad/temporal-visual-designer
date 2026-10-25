# Deployment traceability increment

Branch: `codex/032-deployment-traceability`, based on main `5524cfb` (merged PR #29).

## Implemented boundary

Activation and rollback accept optional change-request number and notes. Strict bounded text metadata is committed with the existing immutable operation receipt and project pointer. Omission/null preserves pre-upgrade request serialization and retry identity. Exact retries retain the original actor/receipt; changed evidence for the same operation conflicts. Existing no-evidence activation responses retain their shape.

New signed history and operation-detail routes expose successful deployment metadata under fresh scoped `run.read`. SQL selects summary fields from stored JSON, excludes release snapshots from response materialization, orders by descending activation revision and uses bounded exclusive cursors. Empty scoped histories and missing-operation 404 responses follow the frozen contract. Existing activation/rollback permissions remain authoritative, including Prod support and no-evidence deployment policy.

See [ADR 0030](../../docs/decisions/0030-deployment-traceability.md), [API contracts](../../docs/api.md) and the [walkthrough](../../docs/deployment-traceability.md). There are no new tables, migrations, dependencies, runtime commands or role grants. Evidence text does not alter release checksums or execution plans. Failed attempts remain in the existing internal audit store.

## Parallel ownership and verification

The domain lane owned `package_promotion/types.py`, `repository.py`, `service.py` and `tests/package_promotion/test_traceability.py`. The HTTP lane owned `apps/api.py`, `api_models.py` and `tests/api/test_deployment_traceability.py`. Independent acceptance owned `tests/api/test_deployment_traceability_acceptance.py` and `tests/runtime/test_deployment_traceability_integration.py`. Interfaces were frozen before parallel implementation; the coordinator owns documentation and final verification.

Focused domain acceptance: 15 passed, including exact legacy request JSON, pre-upgrade receipt retry, evidence restart, cursor bounds, metadata-only selection and fresh authorization. HTTP implementation: 17 new signed tests passed; combined with existing API tests, 37 passed. Independent signed HTTP/SQLite acceptance: 9 passed. Tests include concurrent exact retries, metadata conflicts, cursor stability under later activation, missing/scoped operations, permission revocation, strict nested fields, Prod role policy and provider failures.

The wrapped serializer intentionally omits a return annotation with a narrowly scoped mypy suppression so Pydantic retains the model's typed serialization/OpenAPI schema. Validation and serialization schemas are tested explicitly; the initial generic return-schema issue was fixed before final suites.

Independent implementation reviews found no actionable defects after the schema correction. Ruff lint/format and full mypy (75 source files) passed; source distribution and wheel built. The final default suite passed: 1,212 passed, one Linux-only TCP TIME_WAIT test skipped on Windows, and 39 integration tests deselected (130.22 seconds). The full actual Temporal runtime suite passed: 39 passed, 198 deselected (226.21 seconds), using `.venv/Scripts/python.exe -m pytest -m integration nexusflow/tests/runtime -q`. The new traceability case verifies release A, activation B, rollback A, metadata retrieval, pinned old/new runs, completion and old-history replay.

## Remaining scope

F09-T01c/T02c/T04c/T05d and NF-T013c are checked at this optional-text/history boundary. Full parent tasks remain open. Approval-file upload/download, attachment storage/scanning, package archive transfer, target binding providers, Admin screens and full promotion acceptance remain outstanding. Evidence is opaque text, not external approval verification; authorized deployment without evidence remains supported. No hosted CI, browser, live Entra or production-provider acceptance is claimed.
