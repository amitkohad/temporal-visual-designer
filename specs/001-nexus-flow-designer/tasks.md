# Program tasks and parallel-agent execution

All tasks are planned and unchecked. Implementation is not authorized. Feature task lists are subordinate to these gates; do not execute code tasks merely because they exist.

## Root-owned sequential setup

- [ ] NF-T001 Approve C01–C10 in `contracts.md`, common fixture corpus and ownership map; map NF-001–012 to feature FRs. Prerequisites: requirements review. Owner: contract lead. Acceptance: no unresolved shared shape or ownership conflict; SC-05.
- [ ] NF-T002 Resolve stack, Entra tenancy, expression engine, Python build format, retention and sizing decision gates in `research.md`; select dependency versions and test commands. Prerequisite: NF-T001. Owner: architecture lead. Acceptance: dependent adapters have executable decisions, remaining uncertainties have explicit gates.
- [ ] NF-T003 Establish future `nexusflow/contracts/`, test fixture interfaces, root manifests and per-feature module boundaries. Prerequisite: NF-T002 and later implementation authorization. Owner: integration lead. Acceptance: provider/consumer conformance harness and isolated fixture loading work. No feature agent edits these files.
- [ ] NF-T004 Run runtime feasibility proof from feature 05 before full runtime-dependent integration. Prerequisite: NF-T003. Owner: runtime lead. Acceptance: replay/restart, durable approval delivery/pause, old/new release coexistence pass.

## Parallel work allocation

`[P]` means eligible for concurrent execution only after prerequisites and only with disjoint ownership. It does not mean all local tasks start together. Local contract/setup tasks precede local parallel lanes; local integration tasks follow their providers.

- [ ] NF-T005 [P] Deliver feature 01 isolated acceptance suite. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/01-identity-environments/`; local tasks refine subpaths.
- [ ] NF-T006 [P] Deliver feature 02 isolated suite with identity fake. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/02-project-versioning/`.
- [ ] NF-T007 [P] Deliver feature 03 isolated UI suite with project/catalog fixtures. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/03-designer-forms/`.
- [ ] NF-T008 [P] Deliver feature 04 catalog/import suite with isolated build runner fake. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/04-activity-catalog/`.
- [ ] NF-T009 [P] Deliver feature 05 runtime acceptance. Prerequisites: NF-T003, NF-T004 for full implementation. Owner paths: `nexusflow/modules/05-durable-runtime/`.
- [ ] NF-T010 [P] Deliver feature 06 task suite with directory/runtime/form fixtures. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/06-human-tasks/`.
- [ ] NF-T011 [P] Deliver feature 07 debugger suite with event fixtures. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/07-designer-debugger/`.
- [ ] NF-T012 [P] Deliver feature 08 connector suite with local transports. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/08-connectors/`.
- [ ] NF-T013 [P] Deliver feature 09 promotion suite with registry/runtime fakes. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/09-package-promotion/`.
- [ ] NF-T014 [P] Deliver feature 10 adapter/telemetry tests against synthetic providers. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/10-operations-portability/`. Infrastructure provisioning is outside the current specification scope.

## Integration and convergence

- [ ] NF-T015 Integrate module composition, migrations and navigation under `nexusflow/apps/`, ordered by provider graph in plan.md. Prerequisites: NF-T005–014 and NF-T004. Owner: integration lead. Acceptance: same conformance fixtures pass against actual providers; no fake in deployment configuration.
- [ ] NF-T016 Implement reference-process acceptance fixtures under `nexusflow/tests/system/` for onboarding and purchasing. Prerequisite: NF-T015. Owner: acceptance lead. Acceptance: NF-001–012 and SC-01–04, export/import/Prod authorization, optional evidence, rollback and old-run completion.
- [ ] NF-T017 Verify capacity, tenant isolation, accessibility, recovery and release compatibility under `nexusflow/tests/readiness/`; publish measured results. Prerequisites: NF-T016, approved targets. Owner: readiness lead. Acceptance: SC-06 and no unresolved critical failures. Infrastructure changes require their own reviewed application deployment plan.

## Sub-agent handoff contract

Each assignment contains feature ID, task IDs, approved contract revision, prerequisite evidence, exact writable paths, read-only references, test command and acceptance IDs. Agent returns changed files, test evidence, unresolved issues and proposed contract changes. No agent edits another feature or shared dependency manifest. Coordinator accepts changes before releasing dependents.

Example: after 03 contract setup, run its API/state lane, canvas/form UI lane, and independent test-fixture lane concurrently if their local files do not overlap; perform feature integration only after all three finish. With four execution slots, use one coordinator and up to three active feature/task agents; schedule remaining ready tasks when slots free. Never launch all ten simultaneously or wait on nonexistent providers.

## Experience-design prerequisites

- [ ] NF-T018 Complete UX-T001 foundation and screen-state inventory in Figma; record token/component and ownership references in design-handoff.md. Prerequisite: product/UX brief review. Acceptance: UX-001–012 have screen owners and no shared-frame collision. Design-only work does not require NF-T003 or implementation authorization.
- [ ] NF-T019 Complete UX-T002–004 in parallel on separately owned Figma frames, then UX-T005 usability/accessibility review. Prerequisite: NF-T018. Acceptance: UX-A01–07 reviewed, defects recorded; static designs distinguished from tested prototypes.
- [ ] NF-T020 Freeze UX-T006 handoff in design-handoff.md following user review. Prerequisite: NF-T019. Acceptance: exact approved frame/component references and unresolved limitations recorded. All UI implementation lanes require NF-T020 as an additional prerequisite. Existing backend/test-contract work keeps its original prerequisites. NF-T015/016 must verify NF-013/SC-07 as well as earlier requirements.

Canvas-direction approval recorded 2026-10-23 for frame 5:14. NF-T018–020 remain unchecked because design-system freeze, interaction/state coverage and verification are incomplete. Their review prerequisites must reuse this approval for the canvas rather than request it again; remaining screens and material revisions still require review. Acceptance coverage includes UX-013/014 and UX-A08.
