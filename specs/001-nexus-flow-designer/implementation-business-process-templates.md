# Business-process starter-template increment

Branch: `codex/031-business-process-templates`, based on main `c08a42b` (merged PR #28).

## Implemented boundary

Two pure, versioned built-ins create independent editable Dev drafts: employee onboarding (manager, HR, IT approvals) and purchase requisition (manager, finance approvals). Each requires explicit user/group assignments, has a strict input schema and generates linear managed approval steps with read-only input context, required true confirmation and optional notes. No template contains tenant identities, credentials or external activities.

Signed list/detail APIs require fresh scoped project-read permission. Draft creation requires fresh project-write and Dev, converts strict JSON assignment lists to domain tuples, and uses the ordinary project service for trusted authorship and persistence. Duplicate project IDs conflict. Template creation does not publish, activate, execute or provision tasks. Saved drafts remain independent from the template catalog and each other.

See [ADR 0029](../../docs/decisions/0029-business-process-templates.md), [API contracts](../../docs/api.md) and the [walkthrough](../../docs/business-process-templates.md). No dependency, schema migration or runtime command changes are introduced.

## Parallel ownership and verification

The factory lane owned `designer_forms/templates.py` and `tests/designer_forms/test_templates.py`; the HTTP lane owned `apps/api.py`, `api_models.py` and `tests/api/test_templates.py`; independent acceptance owned `tests/api/test_template_acceptance.py` and `tests/runtime/test_business_templates_integration.py`. Interfaces and task boundaries were frozen before parallel work. The coordinator owns documentation, spec reconciliation and final verification.

Final focused offline acceptance: 39 passed (12.63 seconds), comprising 15 factory/schema/compile tests, 21 signed HTTP tests and 3 independent signed SQLite acceptance cases. Actual JSON assignment arrays are supported through strict transport models. Tests cover invalid inputs/assignments, immutable copies, group/all-approver declarations, trusted authorship, no implicit release/activation/start, fresh revocation, Dev-only creation, strict injection rejection and duplicate-project conflicts.

Independent factory and HTTP reviews found no actionable defects. Ruff lint and formatting passed; full mypy passed for 75 source files. The wheel and source distribution built successfully. The final default suite passed: 1,171 passed, one Linux-only TCP TIME_WAIT test skipped on Windows, and 38 integration tests deselected (181.42 seconds). Earlier runs exposed acceptance-test assumptions about inactive activation state and task response nesting; these assertions were corrected to the existing contracts before final verification.

The final full actual Temporal runtime suite passed: 38 passed, 198 deselected (204.20 seconds), using `.venv/Scripts/python.exe -m pytest -m integration nexusflow/tests/runtime -q`. The two new launcher/socket cases verify signed template creation, publication/activation, manager/HR/IT ordering, input-backed form values, read-only enforcement, required confirmation, background completion delivery and pending-task persistence across restart. Purchase rejection without form submission fails the proof run and creates no finance task. Existing replay and runtime regression cases also remain green.

## Remaining scope

F03-T006a and NF-T007a are checked at this bounded backend-template boundary. Parent F03-T006 remains open for descriptor-backed reference templates, connector/conditional process coverage and React authoring. NF-T016 remains open for complete reference-process system acceptance, cross-environment package promotion and production evidence/rollback coverage.

These starter sequences collect approvals; they do not provision employees or execute purchases. Dates/currencies receive only starter schema constraints, notes remain task data, and rejection uses the existing proof-runtime failed state. No hosted CI, live Entra directory or browser acceptance is claimed.
