# Managed approval form increment

Branch: `codex/027-managed-approval-forms`, from main after PR #24. Decision: [ADR 0025](../../docs/decisions/0025-managed-approval-forms.md).

## Bounded scope

Compile an optional inline form definition and literal initial values on managed approval configuration. Validate and canonicalize at host/worker boundaries. Bind the form using the creation transaction before task visibility, preserving the existing completion destination and exact retry semantics. Approval requires a valid submission; existing rejection and shared-form freeze behavior remain unchanged.

Enable pinned-form field access in the explicit synthetic local task profile. Keep signed task authorization and shared read/write metadata enforcement authoritative. Preserve no-form serialization, historical plans, standalone form-service behavior and all existing runtime acceptance.

## Parallel ownership

The coordinator freezes configuration/helper interfaces and owns local field policy, bootstrap wiring, sample, documentation, checkboxes and aggregate verification. One agent owns atomic form binding and creation refresh; another owns configuration/preflight/activity changes; a third owns independent real CLI/socket/Temporal acceptance. Source and test paths are disjoint. Dependency changes remain coordinator-owned.

## Verification

Verified locally using the existing Python 3.12 environment:

- Atomic binding tests plus existing standalone-form regressions: 30 passed, including 13 new creation-form cases. Runtime configuration lane: 45 focused tests passed, including 14 new cases. Local field-policy tests: three passed.
- New actual CLI/socket/Temporal form acceptance: one passed (28.27 seconds).
- Full actual Temporal runtime suite: 34 passed, 123 deselected (130.91 seconds), retaining all 33 prior runtime/API/launcher/task regressions.
- Final full default suite: 967 passed, one Linux-only test skipped on Windows and 34 integration tests deselected (97.38 seconds).
- Ruff lint and formatting, strict mypy (73 source files), wheel/source distribution builds and `git diff --check` passed.

Independent reciprocal reviews found no atomicity, deadlock, privacy or compatibility blockers. They identified an authorization-provider exception-normalization regression during extraction; the original safe `FormError("unavailable")` behavior was restored and verified with rollback/error-redaction coverage.

Actual acceptance publishes the supplied sample through the signed API, observes the task only after form binding at revision 2, rejects premature approval and invalid/read-only changes, submits valid data at revision 3, edits the source draft, restarts the launcher and verifies the original schema/values remain pinned. Approval at revision 4 completes the real Temporal workflow. Write-only values remain redacted; terminal edits and revoked form access are denied. Unit tests additionally prove exact binding retries do not reset submitted values or terminal decisions, transaction failures leave no visible partial task/form/audit and old no-form approval bytes remain unchanged.

No hosted CI, browser, live Entra or full production-provider acceptance is claimed. Changes remain uncommitted for the next requested commit/PR step.

## Remaining scope

Dynamic workflow input/output mapping, reusable release/project-form resolution, attachments, live field policies, React form authoring/rendering and production integrations remain open. Inline initial values are immutable literals; form submissions are retained in task storage and are not returned to the workflow as outputs. Full feature parents remain unchecked.
