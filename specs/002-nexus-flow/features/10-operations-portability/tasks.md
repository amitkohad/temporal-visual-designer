# Feature 10 tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` tasks own disjoint subtrees after contract freeze. All tasks are future implementation.

- [ ] F10-T01 Freeze `nexusflow/modules/10-operations-portability/contracts/` against `specs/002-nexus-flow/contracts.md`; prerequisites: runtime/identity/release review; acceptance: F10-A01–A05 mapped, no provisioning APIs.
- [ ] F10-T02 [P] Build routing, admission, lifecycle, diagnostics in `nexusflow/modules/10-operations-portability/backend/`; prerequisites: F10-T01; acceptance: F10-A01/A02/A03/A05 with fakes.
- [ ] F10-T03 [P] Build provider conformance bindings in `nexusflow/modules/10-operations-portability/adapters/`; prerequisites: F10-T01 and dependency review; acceptance: F10-A04, no infrastructure files.
- [ ] F10-T04 [P] Build suites and sizing worksheet in `nexusflow/modules/10-operations-portability/tests/`; prerequisites: F10-T01; acceptance: F10-A01–A05, unresolved targets explicit.
- [ ] F10-T05 Assemble provider ports in `nexusflow/modules/10-operations-portability/integration/`; prerequisites: F10-T02/F10-T03/F10-T04 and runtime/identity/registry/task providers; acceptance: F10-A01–A05 with disposable runtime.
- [ ] F10-T06 Record recovery, capacity observations, unresolved production targets, and applicable lint in `nexusflow/modules/10-operations-portability/integration/verification.md`; prerequisites: F10-T05; acceptance: functional scenarios pass, production gate pending sizing, zero deployment modifications.
