# React templates + decision authoring (Phase 3.2)

Branch `feature/phase-32-templates-decisions-3954` based on main after Phase 3.1
forms (#74). Mocked HTTP / Vitest / isolated graph unit tests only; no Entra,
Temporal, or production-provider claim.

## Delivered

- **Descriptor-backed template picker:** `template-api.ts` accepts any well-formed
  catalog `TemplateInfo` (no hardcoded two-ID allowlist). Projects shows template
  ID, workflow, version, assignment slots, and start-input schema fields, plus
  per-slot approval rules (`any_one` / `all_approvers`).
- **Decision authoring subset:** insert decision (approved/rejected branches),
  rename branch ports, optional `input_mapping.route` with frozen
  `approval_outcome` shape only. Honest UI limits: not a general expression
  language; proof.v1 cannot execute decision nodes.
- **Authoring validation (T002b slice):** `designer_forms/graph.py` validates the
  approval_outcome route subset when present.

## Verification (this change)

- Vitest: `template-api`, `workflow-edit`, `Projects` — template descriptor +
  rule create, decision insert/route save.
- Pytest: `test_decision_approval_outcome_route_must_reference_approval`.

## Remaining (not claimed)

- Published activity-descriptor / connector-backed reference templates (Phase 3.3+).
- General expression language / runtime evaluation of decision routes.
- proof.v1 interpreter support for decision branches.
- Full F03-T006b / F03-T002b parent acceptance.
