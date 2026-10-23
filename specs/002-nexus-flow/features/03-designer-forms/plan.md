# Feature 03 implementation plan

## Ownership

Own future paths `nexusflow/modules/03-designer-forms/contracts/`, `backend/`, `ui/`, `templates/`, `tests/`, and `integration/` beneath that module. Consult [shared contracts](../../contracts.md). Freeze workflow/form/expression versions before parallel work; changes go through the coordinator. UI uses fake catalog/project/task/debugger ports, so sibling implementation is not a prerequisite for authoring tests.

## Design and interfaces

Expose pure workflow/form validation, form rendering data, and authorized form-submission validation. Distinguish executable graph from layout metadata. Use explicit branch scope and join validation, bounded loops, and acyclic reusable-workflow dependencies. Use a typed, constrained expression AST with identical versioned semantics in authoring and execution; agree its contract with feature 05 before freezing. Avoid a second independently invented runtime language.

Palette descriptors come from feature 04, revisions from feature 02, task permissions/submission from feature 06, debug navigation from feature 07, and activity execution semantics from feature 08. Every port has deterministic fixtures. Renderer returns only permitted fields; authoritative submit handlers re-check identity, allowed fields, and validation. File upload itself is delegated to the task/attachment service; the form stores references.

## Parallel lanes

After contract freeze, graph validation, form validation/rendering, canvas UI, form-builder UI, template assets, and acceptance tests have separate directory ownership. Backend lanes keep unit tests locally. UI lanes communicate through contracts and do not edit shared navigation or each other's components. Integrator owns module wiring and later adapters. Do not modify Temporal server code.

## Test strategy

Use deterministic graph fixtures for scope, type, join, loop, and recursion failures. Exercise browser author/save/reload, form keyboard accessibility, field-error focus, malicious content, and save-conflict recovery with fake APIs. Test server form validation independently of the browser. Compare expression fixtures against runtime conformance without requiring a running Temporal cluster. Acceptance fixtures cover the two business templates without sending notifications. Application tests use the commands and integration conventions approved during stack setup.

## Figma implementation handoff

Use [UX specification](../../ux-design.md) and [handoff register](../../design-handoff.md) for UX-S01–03. Design is a separate phase before UI code. Freeze component/token references before parallel canvas and form lanes; a shared UI foundation has one owner. Layout must support contextual insertion, keyboard outline, semantic errors, zoom and collapsed panels. Add browser acceptance for F03-A08 and UX-A01–03/A07. Do not reproduce static concepts as flattened images.

The user approved spatial frame 5:14 visual direction on 2026-10-23. Preserve compact icon nodes, event/decision shapes, labeled routed edges, slim rails, selection inspector and minimap. Complete component/state/keyboard handoff under the existing gates; do not substitute the superseded stacked-card concept. Authoritative approval scope is design-handoff.md.
