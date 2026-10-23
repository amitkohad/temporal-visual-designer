# Feature 03 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Implementation status is tracked below; checked subtasks record verified backend delivery without closing remaining UI/runtime scope. Parallel tasks own disjoint paths; no shared barrel files or application navigation changes until integration.

- [ ] **F03-T001** Freeze workflow/form/validation/expression contracts and fixtures in `nexusflow/modules/03-designer-forms/contracts/`, coordinating runtime expression semantics. Prerequisites: NF-T003; fake project/catalog/task/debugger ports suffice. Maps: F03-A01–A07.
  - [x] **F03-T001a** Versioned graph/binding and form definition/preview/submission contracts, JSON Schema exports and deterministic fixtures are implemented (`types.py`, `form_types.py`, `export.py`, `form_export.py`).
  - [ ] **F03-T001b** Freeze general expression semantics with runtime execution and remaining full-feature contracts/fixtures.
- [ ] **F03-T002 [P]** Implement graph/type/scope validation and local unit tests in `nexusflow/modules/03-designer-forms/backend/graph/`. Prerequisites: F03-T001. Maps: F03-A02, A03, A07.
  - [x] **F03-T002a** Graph structure, reusable-reference recursion, typed input mappings and branch/output scope validation pass isolated tests (`graph.py`, `mappings.py`).
  - [ ] **F03-T002b** Complete general expression validation and shared runtime semantics; verify remaining template/full-feature cases.
- [ ] **F03-T003 [P]** Implement authoritative form validation and rendering rules with local tests in `nexusflow/modules/03-designer-forms/backend/forms/`. Prerequisites: F03-T001. Maps: F03-A04, A05.
  - [x] **F03-T003a** Authoritative server form-definition and submission validation, conditional rules, permission-filtered previews, partial merges and guarded commits pass isolated tests (`form_definition.py`, `forms.py`).
  - [ ] **F03-T003b** Complete rendering/layout and attachment semantics, task/start adapters and shared expression integration; browser rendering is also tracked by T005.
- [ ] **F03-T004 [P]** Implement project explorer, canvas, properties, mappings, and save recovery with component tests in `nexusflow/modules/03-designer-forms/ui/canvas/`. Prerequisites: F03-T001. Maps: F03-A01–A03, A06. Use fake APIs.
- [ ] **F03-T005 [P]** Implement form builder, preview, and accessible renderer with component tests in `nexusflow/modules/03-designer-forms/ui/forms/`. Prerequisites: F03-T001. Maps: F03-A04, A05. Use fake validation/submission ports.
- [ ] **F03-T006 [P]** Author onboarding and requisition templates in `nexusflow/modules/03-designer-forms/templates/`. Prerequisites: F03-T001. Maps: F03-A07. Use frozen descriptor fixtures.
- [ ] **F03-T007 [P]** Implement isolated browser/security/contract acceptance harness in `nexusflow/modules/03-designer-forms/tests/acceptance/`. Prerequisites: F03-T001. Maps: F03-A01–A07. Do not edit implementation paths.
  - [x] **F03-T007a** Independent backend graph/mapping/publication and form security/contract acceptance suites pass in `nexusflow/tests/designer_forms/`.
  - [ ] **F03-T007b** Implement browser/keyboard/rendered-content security acceptance and remaining F03-A01–A09 coverage.
- [ ] **F03-T008** Wire module and sibling adapters in `nexusflow/modules/03-designer-forms/integration/`; verify all acceptance fixtures and applicable lint; record results in `nexusflow/modules/03-designer-forms/tests/verification.md`. Prerequisites: F03-T002–T007; actual sibling adapters only needed for cross-feature integration. Maps: F03-A01–A07.
  - [x] **F03-T008a** Local project-publication composition uses actual SQLite revisions and authoring/form validators, with isolated identity/catalog/task ports; publication regressions pass.
  - [ ] **F03-T008b** Wire actual sibling providers and browser module, then verify all acceptance/UX scenarios and applicable lint.

## Additional UI gate

F03-T004/F03-T005 require NF-T020 as well as F03-T001 and must implement F03-FR10/F03-A08. F03-T007 may author UX fixtures earlier, but integrated browser acceptance requires the approved designs and completed UI. F03-T008 additionally verifies F03-A08/UX-A01–03/A07. Figma design-only work is tracked separately by UX-T001–006 and is not blocked by the implementation gate above.

Reference revision: F03-T004 additionally implements F03-FR11 and UX-013/014 after the design gate. F03-T007/F03-T008 verify F03-A09/UX-A08. Use spatial frame 5:14 in the handoff register, not the superseded stacked-card frame.

## Backend evidence and remaining scope

Checked subtasks above reflect verified delivery, rather than leaving completed work invisible inside an open parent. Actual paths and acceptance evidence are in [implementation-authoring-validation.md](../../implementation-authoring-validation.md) and [implementation-form-validation.md](../../implementation-form-validation.md). The latest form increment records 365 isolated tests on Python 3.12/3.13 and six Temporal regression tests. Parent tasks stay open where their original full acceptance includes general expression/runtime semantics, rendering/attachments, actual adapters or browser coverage. React UI, reference templates and full sibling integration remain pending.
