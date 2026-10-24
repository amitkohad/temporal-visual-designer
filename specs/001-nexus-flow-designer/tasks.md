# Program tasks and parallel-agent execution

Foundation implementation was authorized on 2026-10-23. See [implementation-foundation.md](implementation-foundation.md) for completed NF-T003 subtasks and verification. Completed tasks and independently verified subtasks are checked; parent tasks remain open where their original acceptance is not yet met. Update these checkboxes whenever implementation is confirmed, with evidence and remaining scope. Feature tasks remain subordinate to scope-specific contract and decision gates.

## Root-owned sequential setup

- [ ] NF-T001 Approve C01–C10 in `contracts.md`, common fixture corpus and ownership map; map NF-001–012 to feature FRs. Prerequisites: requirements review. Owner: contract lead. Acceptance: no unresolved shared shape or ownership conflict; SC-05.
  - [x] NF-T001a Foundation request/error/activity/workflow/project/event contracts, fixture corpus and module ownership boundaries are executable (ADR 0001; `nexusflow/contracts/` and foundation tests).
  - [ ] NF-T001b Approve remaining shared C01–C10 shapes and resolve cross-feature ownership/contract gaps.
- [ ] NF-T002 Resolve stack, Entra tenancy, expression engine, Python build format, retention and sizing decision gates in `research.md`; select dependency versions and test commands. Prerequisite: NF-T001. Owner: architecture lead. Acceptance: dependent adapters have executable decisions, remaining uncertainties have explicit gates.
  - [x] NF-T002a Python backend/React browser stack, locked dependency versions and executable development checks are selected; Temporal proof SDK decision is verified (ADRs 0001/0002).
  - [ ] NF-T002b Resolve remaining expression engine, Entra topology/live integration, activity sandbox/format extensions, retention and sizing decisions at affected gates.
  - [x] NF-T002c Source-bundle format, logical digest and configurable offline intake limits are selected and tested (ADR 0007); worker sandbox decisions remain open.
- [x] NF-T003 Establish future `nexusflow/contracts/`, test fixture interfaces, root manifests and per-feature module boundaries. Prerequisite: NF-T002 and later implementation authorization. Owner: integration lead. Acceptance: provider/consumer conformance harness and isolated fixture loading work. No feature agent edits these files.
- [x] NF-T004 Run runtime feasibility proof from feature 05 before full runtime-dependent integration. Prerequisite: NF-T003. Owner: runtime lead. Acceptance: replay/restart, durable approval delivery/pause, old/new release coexistence pass.

## Parallel work allocation

`[P]` means eligible for concurrent execution only after prerequisites and only with disjoint ownership. It does not mean all local tasks start together. Local contract/setup tasks precede local parallel lanes; local integration tasks follow their providers.

- [ ] NF-T005 [P] Deliver feature 01 isolated acceptance suite. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/01-identity-environments/`; local tasks refine subpaths.
  - [x] NF-T005a Identity backend isolated acceptance for signed tokens, policy, directory failure/revocation and logical placement (`nexusflow/tests/identity/`).
  - [ ] NF-T005b Complete HTTP/download/stream acceptance required by F01-T004; UI/provider integration has separate feature gates.
- [x] NF-T006 [P] Deliver feature 02 isolated suite with identity fake. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/02-project-versioning/`.
- [ ] NF-T007 [P] Deliver feature 03 isolated UI suite with project/catalog fixtures. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/03-designer-forms/`.
- [x] NF-T008 [P] Deliver feature 04 catalog/import suite with isolated compatibility/capability fakes. The approved preassembled source format (ADR 0007) performs no builds or installation during intake; executable worker acceptance remains F04-T007. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/04-activity-catalog/`.
  - [x] NF-T008a Validated Python source-bundle intake and independent security/recovery suite are delivered (F04-T001a/T002/T005a).
  - [x] NF-T008b Catalog publication/lifecycle/export acceptance is complete (F04-T003/T005). Isolated execution remains explicitly open under F04-T007; it is not inferred from metadata/capability fakes.
- [ ] NF-T009 [P] Deliver feature 05 runtime acceptance. Prerequisites: NF-T003, NF-T004 for full implementation. Owner paths: `nexusflow/modules/05-durable-runtime/`.
  - [x] NF-T009a Isolated durable start contracts and SQLite deduplication/pinned-plan recovery are implemented and verified with required provider fakes (RT-T01a/T03a/T04a; [evidence](implementation-durable-starts.md)).
  - [x] NF-T009c Async proof-runtime Temporal start dispatch and permanent journal reconciliation are verified with SDK-failure and actual local Temporal tests (RT-T01c/T03c/T04c; [evidence](implementation-temporal-start-dispatch.md)).
  - [ ] NF-T009b Complete full interpreter, actual start/provider adapters, checkpoint/projection and RT-A1–A7 acceptance.
- [ ] NF-T010 [P] Deliver feature 06 task suite with directory/runtime/form fixtures. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/06-human-tasks/`.
  - [x] NF-T010a Verify bounded any-one/all-approver core, fresh trusted authorization, immutable assignment snapshots, SQLite decision/outbox atomicity, command retries, concurrency and recovery against fake ports ([evidence](implementation-human-task-core.md)).
  - [x] NF-T010c Verify assigned-task pagination and authenticated task HTTP composition with scoped signed identities, object-ID eligibility, local SQLite persistence and safe failures ([evidence](implementation-human-task-api.md)).
  - [ ] NF-T010b Complete forms/attachments, timers/escalation/notifications, sequential/conditional rules, task lifecycle, remaining inbox filters/UI and full HT-A1–A7 acceptance with actual adapters.
- [ ] NF-T011 [P] Deliver feature 07 debugger suite with event fixtures. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/07-designer-debugger/`.
- [ ] NF-T012 [P] Deliver feature 08 connector suite with local transports. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/08-connectors/`.
- [ ] NF-T013 [P] Deliver feature 09 promotion suite with registry/runtime fakes. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/09-package-promotion/`.
  - [x] NF-T013a Target-local whole-project activation/rollback, compatibility/authorization, conservative retention, atomic pointer/audit, retries and actual start resolution are independently verified ([evidence](implementation-project-activation.md)).
  - [ ] NF-T013b Complete package archive transfer, binding/evidence persistence, Admin UI and full F09 acceptance.
- [ ] NF-T014 [P] Deliver feature 10 adapter/telemetry tests against synthetic providers. Prerequisite: NF-T003. Owner paths: `nexusflow/modules/10-operations-portability/`. Infrastructure provisioning is outside the current specification scope.

## Integration and convergence

- [ ] NF-T015 Integrate module composition, migrations and navigation under `nexusflow/apps/`, ordered by provider graph in plan.md. Prerequisites: NF-T005–014 and NF-T004. Owner: integration lead. Acceptance: same conformance fixtures pass against actual providers; no fake in deployment configuration.
  - [x] NF-T015a Compose initial authenticated Python HTTP project/catalog/activation/start/status API using actual local module providers and an actual Temporal gateway; verify signed request isolation and HTTP-to-Temporal acceptance ([evidence](implementation-application-api.md)).
  - [ ] NF-T015b Complete remaining provider/HTTP/download/stream composition, production migrations, React navigation and full feature conformance. NF-T015a/c are bounded provider-subset integrations, not full NF-T005–014 completion.
  - [x] NF-T015c Deliver explicit persistent local-demo init/token/serve/stop composition, actual loopback API/Temporal proof worker, safe seed and exclusive lifecycle ownership; verify socket/restart and isolated bootstrap/runtime acceptance ([evidence](implementation-local-launcher.md)).
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

NF-T004 evidence is in [implementation-runtime-proof.md](implementation-runtime-proof.md). Full feature 05 and UI tasks remain open; the proof does not complete every runtime acceptance scenario.

Feature 01 backend core and isolated backend acceptance are checked; NF-T005 remains open specifically for route/download/stream acceptance. NF-T006 is complete for its stated isolated-suite scope; Feature 02 UI and full integration remain open. See [implementation-identity-environments.md](implementation-identity-environments.md).

Checkbox reconciliation (2026-10-23): NF-T003 is closed by its local conformance harness/fixture-loading acceptance (NF-T003a–d); broader actual-provider composition remains NF-T015. F02-T001/T002/T003/T005 and F01-T002 are closed at their stated backend boundaries. See their feature task lists for evidence and explicitly unchecked remaining work. No hosted CI result is inferred from local verification.
