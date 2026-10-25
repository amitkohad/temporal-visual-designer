# Portable project-package export increment

Branch: `codex/034-project-package-export`, based on main `441247f` (merged PR #31).

## Implemented boundary

Published releases export as deterministic whole-project ZIPs containing the full authored project snapshot, exact Python activity bundles and a versioned manifest with file/project/executable/package digests. Optional test definitions and layout are preserved as authored. Server-owned environment/configuration values, tokens, run payloads, deployment evidence and actors are never queried for packaging. The format does not certify arbitrary authored strings as secret-free.

Signed export requires fresh `package.export`; validation requires fresh `package.import` and a matching organization declaration. Validation returns manifest metadata and performs no project/artifact/release/activation/runtime writes. It checks strict ZIP inventory, duplicate/unsafe JSON, resource limits, checksums, source-bundle identities and baseline project publication rules. No extraction, installation or Python execution occurs. Same-organization metadata and checksums are not source signatures or executable trust.

See [ADR 0032](../../docs/decisions/0032-project-package-export.md), [API contracts](../../docs/api.md) and the [walkthrough](../../docs/project-packages.md). Defaults distinguish the 16 MiB codec archive limit from the existing 6,000,000-byte HTTP validation-upload ceiling. Nested source validation is sequential and stops at the aggregate budget; encode rejects oversized content before inventory hashing. No dependencies, database migrations or runtime commands change.

## Parallel ownership and verification

The codec lane owned `package_promotion/package_format.py` and `tests/package_promotion/test_package_format.py`. The service/API lane owned `package_promotion/packages.py`, `apps/api.py`, `api_backend.py` and `tests/api/test_project_packages.py`. Independent acceptance owned `tests/api/test_project_package_acceptance.py` and `tests/runtime/test_project_package_integration.py`. Interfaces were frozen before parallel work; interrupted work resumed on the same branch after the usage limit reset. The coordinator owns documentation, spec reconciliation and final verification.

Focused codec tests: 36 passed. Service/API tests: 20 passed, plus 20 existing API regressions. Independent signed HTTP/SQLite acceptance: 9 passed. Coverage includes complete resources/source bytes, deterministic/repacked identity, invalid inventories/digests/dependencies, ZIP/JSON adversarial fixtures, early configured limits, invalid baseline project structure and prohibited environment-value keys, scoped permissions/revocation, Prod policy, immutable exports after draft edits and inspection without target providers or mutation.

Independent codec/service reviews found no actionable defects after early-budget corrections. Ruff lint/format and full mypy (78 source files) passed; wheel and source distribution built. The final default suite passed: 1,314 passed, one Linux-only TCP TIME_WAIT test skipped on Windows, and 41 integration tests deselected (150.74 seconds). The full actual Temporal runtime suite passed: 41 passed, 198 deselected (194.89 seconds), using `.venv/Scripts/python.exe -m pytest -m integration nexusflow/tests/runtime -q`. The new case proves export/validation while A waits, B activation, byte-identical re-export of A, preserved pinning, completion and old-history replay.

## Remaining scope

F09-T01e/T02e/T04e/T05f and NF-T013e are checked at the portable export/inspection boundary. Target import/persistence, operation-id recovery for target writes, configuration binding, provenance/executable trust, worker readiness, production providers and Admin UI remain outstanding. This exporter rejects withdrawn artifacts and exposes no caller-supplied retention reference; retained-artifact export orchestration remains separate work. Parent promotion tasks remain open. No hosted CI, browser, live Entra or target-deployment acceptance is claimed.
