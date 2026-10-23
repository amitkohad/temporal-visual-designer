# Feature 03 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

Future implementation only. Parallel tasks own disjoint paths; no shared barrel files or application navigation changes until integration.

- [ ] **F03-T001** Freeze workflow/form/validation/expression contracts and fixtures in `nexusflow/modules/03-designer-forms/contracts/`, coordinating runtime expression semantics. Prerequisites: NF-T003; fake project/catalog/task/debugger ports suffice. Maps: F03-A01–A07.
- [ ] **F03-T002 [P]** Implement graph/type/scope validation and local unit tests in `nexusflow/modules/03-designer-forms/backend/graph/`. Prerequisites: F03-T001. Maps: F03-A02, A03, A07.
- [ ] **F03-T003 [P]** Implement authoritative form validation and rendering rules with local tests in `nexusflow/modules/03-designer-forms/backend/forms/`. Prerequisites: F03-T001. Maps: F03-A04, A05.
- [ ] **F03-T004 [P]** Implement project explorer, canvas, properties, mappings, and save recovery with component tests in `nexusflow/modules/03-designer-forms/ui/canvas/`. Prerequisites: F03-T001. Maps: F03-A01–A03, A06. Use fake APIs.
- [ ] **F03-T005 [P]** Implement form builder, preview, and accessible renderer with component tests in `nexusflow/modules/03-designer-forms/ui/forms/`. Prerequisites: F03-T001. Maps: F03-A04, A05. Use fake validation/submission ports.
- [ ] **F03-T006 [P]** Author onboarding and requisition templates in `nexusflow/modules/03-designer-forms/templates/`. Prerequisites: F03-T001. Maps: F03-A07. Use frozen descriptor fixtures.
- [ ] **F03-T007 [P]** Implement isolated browser/security/contract acceptance harness in `nexusflow/modules/03-designer-forms/tests/acceptance/`. Prerequisites: F03-T001. Maps: F03-A01–A07. Do not edit implementation paths.
- [ ] **F03-T008** Wire module and sibling adapters in `nexusflow/modules/03-designer-forms/integration/`; verify all acceptance fixtures and applicable lint; record results in `nexusflow/modules/03-designer-forms/tests/verification.md`. Prerequisites: F03-T002–T007; actual sibling adapters only needed for cross-feature integration. Maps: F03-A01–A07.

## Additional UI gate

F03-T004/F03-T005 require NF-T020 as well as F03-T001 and must implement F03-FR10/F03-A08. F03-T007 may author UX fixtures earlier, but integrated browser acceptance requires the approved designs and completed UI. F03-T008 additionally verifies F03-A08/UX-A01–03/A07. Figma design-only work is tracked separately by UX-T001–006 and is not blocked by the implementation gate above.

Reference revision: F03-T004 additionally implements F03-FR11 and UX-013/014 after the design gate. F03-T007/F03-T008 verify F03-A09/UX-A08. Use spatial frame 5:14 in the handoff register, not the superseded stacked-card frame.
