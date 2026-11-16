# ADR 0071: Form start contracts

Status: accepted for the RT-T01b form-start subset (register/disable + signed submit
into durable starts). Webhook starts are ADR 0070. Full interpreter, checkpoints and
projections remain open under RT-T01b/RT-T03b parents.

## Decision

Form starts reuse the existing durable start boundary ([ADR 0009](0009-durable-starts.md)).
Each accepted submission becomes a `StartRequest` with `source="form"` and a
caller-supplied idempotency key. Authorization uses the signed session `run.start`
permission (same mutate surface as schedules). Active-release snapshotting, input
fingerprinting and at-least-once dispatch semantics are unchanged.

### Definition

A form-start record is scoped to organization/environment/project/workflow:

- `form_start_id` — Identifier; unique within the org/env scope
- `form_definition_json` — pinned `FormDefinition` 0.1 snapshot (canonical JSON);
  validates submissions against its `data_schema` (+ validation_rules schemas)
- `enabled` — disabled form starts reject submit; create/list/disable are the mutating surface

Pinned definitions reuse the existing managed/authoring `FormDefinition` contract
([project versioning types](../../nexusflow/modules/project_versioning/types.py)).
This slice does not invent a new Designer form builder; callers supply an already-valid
snapshot (or a minimal object schema form).

### Submit

Signed HTTP submit carries:

- `idempotency_key` — Identifier (≤128) used as the start key
- `values` — JSON object validated against the pinned form schemas before start

Validated values are canonicalized and become the workflow start `input_json`.
Reuse of the same key with a different payload returns start `conflict`.

### Errors

Typed form-start errors: `invalid_input`, `invalid_definition`, `not_found`,
`conflict`, `forbidden`, `unavailable`.

### Local enablement

`nexusflow-local enable-form-starts` writes a Dev-only profile marker and form-starts
database. `serve` loads the profile and exposes signed create/list/disable/submit.
Without the profile, form-start APIs are unavailable.

## Non-claims

No Entra, attachment uploads, expression-driven visibility on the start path beyond
schema validation, webhook adapters (ADR 0070), checkpoints, projections or Designer
palette UI (Phase 3.10). This slice does not complete RT-T01b or RT-T03b parents.
