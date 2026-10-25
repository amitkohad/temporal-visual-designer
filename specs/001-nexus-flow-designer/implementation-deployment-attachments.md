# Deployment evidence-file increment

Branch: `codex/033-deployment-attachments`, based on main `9cf85f8` (merged PR #30).

## Implemented boundary

Optional files attach to existing successful deployment operations through signed PUT/list/download routes. Upload requires fresh scoped `package.activate`; reads require fresh `run.read`. Files are separate from deployment requests and do not block activation or change text evidence, receipts, release checksums, active pointers or execution plans.

An additive local SQLite table links files to the scoped operation through a foreign key. Each file permits 1–2,097,152 opaque bytes, with at most 10 attachments per operation. A single immediate transaction checks existence, immutable retry identity and quota, then commits bytes and trusted uploader metadata. Exact retries preserve original actor/time even at capacity; changed bytes or label conflict. There is no overwrite/delete endpoint. The existing activation database now includes these blobs and must be retained during backup/recovery.

Metadata listing excludes file content. Download validates stored size/SHA-256 before returning opaque bytes with a fixed attachment filename, nosniff, no-store and correlation headers. Display names are labels, never paths or response filenames. No source/file execution, extraction, installation or inline rendering is introduced. This is bounded local storage, not an approval authenticity or malware-verification service.

See [ADR 0031](../../docs/decisions/0031-deployment-attachments.md), [API contracts](../../docs/api.md) and the [walkthrough](../../docs/deployment-attachments.md). No dependency, role or runtime-command changes are required. Existing installations gain the new table through additive initialization; production provider migrations remain separate work.

## Parallel ownership and verification

The domain lane owned `package_promotion/attachments.py`, `repository.py` and `tests/package_promotion/test_attachments.py`. The HTTP lane owned `apps/api.py`, `api_backend.py` and `tests/api/test_deployment_attachments.py`. Independent acceptance owned `tests/api/test_deployment_attachments_acceptance.py` and `tests/runtime/test_deployment_attachments_integration.py`. Interfaces, limits, permissions and retry semantics were frozen before parallel work. The coordinator owns documentation and final verification.

Domain tests: 19 passed; complete package-promotion suite: 58 passed (10.94 seconds). Tests include a 15-connection quota race, retry at quota, lost-commit acknowledgment, original actor retention, reopened/legacy stores, foreign keys, metadata-only queries, corrupted downloads and storage failure. New signed HTTP tests: 12 passed, plus 37 existing API regressions. Independent signed HTTP/SQLite acceptance: 6 passed, covering opaque bytes, safe headers, exact/chunked size bounds, quota retries, missing operations, scope/revocation, forged Prod roles and sanitized provider errors.

Independent domain and HTTP reviews found no actionable defects and reran the 19/12 focused suites successfully. Ruff lint/format and full mypy (76 source files) passed; source distribution and wheel built. The final default suite passed: 1,249 passed, one Linux-only TCP TIME_WAIT test skipped on Windows, and 40 integration tests deselected (139.86 seconds). The full actual Temporal runtime suite passed: 40 passed, 198 deselected (193.89 seconds), using `.venv/Scripts/python.exe -m pytest -m integration nexusflow/tests/runtime -q`. The new case verifies an attachment to operation A after activation B preserves A's receipt, leaves B active, keeps both runs pinned, and allows completion/replay.

## Remaining scope

F09-T01d/T02d/T04d/T05e and NF-T013d are checked at this local attachment boundary. Parent promotion tasks remain open for package transfer, production binding/artifact providers, Admin UI and full cross-environment conformance. Range/stream downloads, attachment edit/delete, inline preview and production scanning policy are outside this increment. No hosted CI, browser, live Entra or production-provider acceptance is claimed.
