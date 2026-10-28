# Frozen workflow-input inspection increment

Branch: `codex/040-debug-input-inspection`, based on main `0a3cb42` (merged PR #37).

## Implemented boundary

Signed Dev sessions expose read-only frozen workflow inputs. Fresh debug.execute, Dev/owner/placement and persisted integrity checks precede source access. The exact original revision/workflow and proof definition digest govern redaction. Current drafts, mutable readiness and runtime are not queried. Missing/inconsistent source returns sanitized unavailable errors; edits cannot declassify older sessions.

Pure projection exposes only primitive fields with exact x-nexusflow-inspect:true. Protected flags/password format override public annotations; malformed protected flags fail closed. Objects permit declared-property traversal, not blanket disclosure. Arrays, unsupported/ref/composed/conditional/dynamic schemas, unclassified values and unknown keys stay hidden. Unknown key names do not appear in metadata. Frozen plan/request inputs must match canonically.

Limits cover the whole serialized response (16 KiB), scalar JSON (1,024 bytes), depth eight, 1,000 visited nodes and 100 deterministic escaped redaction pointers. Budget overflow omits data and reports truncation, with empty-root fallback for aggregate overflow. Inspection resolves no secrets/connections, returns no step/activity outputs and changes no gates, receipts or activation.

No persisted formats, worker commands, start fingerprints or dependencies change. Enabled local debugging gains the route automatically. See [ADR 0038](../../docs/decisions/0038-debug-input-inspection.md), [guide](../../docs/debug-input-inspection.md), [API](../../docs/api.md) and [sample](../../examples/local-debug-input-project.json).

## Parallel ownership and verification

Frozen interfaces preceded redaction/types/unit, service/API/tests and independent HTTP/Temporal lanes. The coordinator owns documentation/checklists, example validation and global verification.

Redaction: 42 tests passed. Signed API: 12 passed. Independent HTTP: 4 passed. New real Temporal: 1 passed (15.97 seconds), verifying paused inspection without gate changes, original schema despite draft edits, worker-down reads, completed inspection and history replay. Coverage includes protected/unknown-name hiding, malformed/unsupported schemas, budgets/escaped paths, scope/source/hash failures, fresh owner/revocation and no runtime/readiness writes. The sample passes baseline project checks and proof compilation without execution.

Full default/runtime regression and final quality/build results are recorded after completion.

## Remaining scope

DB-T01d/T02d/T04d/T05c and NF-T011d are checked for this independently verified input subset; full parents remain open. Arrays/composed-schema inspection, runtime variables, step inputs/outputs, broader redaction policy, events, cancellation/expiry, mocks, parallel stepping and React controls remain outstanding. Public annotations classify caller-supplied workflow inputs; they grant no access to environment secrets or runtime payloads. Synthetic identity/readiness and local Temporal do not establish live Entra, browser or production-provider acceptance.
