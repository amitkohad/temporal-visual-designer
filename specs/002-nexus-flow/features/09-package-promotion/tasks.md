# Feature 09 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` tasks run concurrently only after prerequisites and own separate subtrees. These are future implementation tasks.

- [ ] F09-T01 Freeze `nexusflow/modules/09-package-promotion/contracts/` against `specs/002-nexus-flow/contracts.md`; prerequisites: release/registry/runtime/authorization review; acceptance: F09-A01–A06 mapped, evidence optional.
- [ ] F09-T02 [P] Build lifecycle and audit in `nexusflow/modules/09-package-promotion/backend/`; prerequisites: F09-T01; acceptance: F09-A01–A06 through fake ports.
- [ ] F09-T03 [P] Build Admin screens in `nexusflow/modules/09-package-promotion/ui/`; prerequisites: F09-T01; acceptance: F09-A01/A02/A04/A06 using stubs.
- [ ] F09-T04 [P] Build fixtures and acceptance suites in `nexusflow/modules/09-package-promotion/tests/`; prerequisites: F09-T01; acceptance: F09-A01–A06 including post-commit retry.
- [ ] F09-T05 Assemble real provider ports in `nexusflow/modules/09-package-promotion/integration/`; prerequisites: F09-T02/F09-T03/F09-T04 and registry/runtime/identity providers ready; acceptance: F09-A01–A06 across environments.
- [ ] F09-T06 Record acceptance and applicable lint in `nexusflow/modules/09-package-promotion/integration/verification.md`; prerequisites: F09-T05; acceptance: no mixed activation or secret export, all scenarios pass.
