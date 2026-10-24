# Feature 09 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` tasks run concurrently only after prerequisites and own separate subtrees. These are future implementation tasks.

- [ ] F09-T01 Freeze `nexusflow/modules/09-package-promotion/contracts/` against `specs/001-nexus-flow-designer/contracts.md`; prerequisites: release/registry/runtime/authorization review; acceptance: F09-A01–A06 mapped, evidence optional.
  - [x] F09-T01a Freeze target-local activation/rollback requests, revisions, operation receipts, readiness/configuration and audit contracts (ADR 0011).
  - [ ] F09-T01b Freeze archive import/export, evidence/attachment and remaining cross-environment contracts.
- [ ] F09-T02 [P] Build lifecycle and audit in `nexusflow/modules/09-package-promotion/backend/`; prerequisites: F09-T01; acceptance: F09-A01–A06 through fake ports.
  - [x] F09-T02a Implement local SQLite whole-project activation/rollback, fresh authorization, exact compatibility checks, conservative retention, atomic success audit and idempotent operation recovery.
  - [ ] F09-T02b Implement portable package import/export, evidence storage, target binding persistence and remaining lifecycle/recovery operations.
- [ ] F09-T03 [P] Build Admin screens in `nexusflow/modules/09-package-promotion/ui/`; prerequisites: F09-T01; acceptance: F09-A01/A02/A04/A06 using stubs.
- [ ] F09-T04 [P] Build fixtures and acceptance suites in `nexusflow/modules/09-package-promotion/tests/`; prerequisites: F09-T01; acceptance: F09-A01–A06 including post-commit retry.
  - [x] F09-T04a Verify backend activation, concurrent/duplicate requests, lost-commit-response recovery, rollback incompatibility, no-evidence Prod activation authorization, immutable start binding and source/artifact retention.
  - [ ] F09-T04b Complete full package transfer, evidence attachment, UI and F09-A01–A06 end-to-end acceptance.
- [ ] F09-T05 Assemble real provider ports in `nexusflow/modules/09-package-promotion/integration/`; prerequisites: F09-T02/F09-T03/F09-T04 and registry/runtime/identity providers ready; acceptance: F09-A01–A06 across environments.
  - [x] F09-T05a Compose actual local project/catalog/identity stores and start resolver; verify activation/rollback coexistence and replay against real local Temporal with explicit configuration/capability fakes.
  - [ ] F09-T05b Complete secure package transfer, actual configuration/capability providers, Admin UI and cross-environment acceptance.
- [ ] F09-T06 Record acceptance and applicable lint in `nexusflow/modules/09-package-promotion/integration/verification.md`; prerequisites: F09-T05; acceptance: no mixed activation or secret export, all scenarios pass.

Bounded activation evidence: [implementation record](../../implementation-project-activation.md). The F09-T01a contract subset gates T02a/T04a/T05a only. These local backend subtasks do not require UI completion; full parent tasks remain open. Python implementations live in `nexusflow/modules/package_promotion/` and tests in `nexusflow/tests/package_promotion/`.
