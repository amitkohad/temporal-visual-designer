# Durable runtime tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` permits concurrent execution only after listed prerequisites. Paths are future ownership boundaries. No code is requested now.

- [ ] **RT-T01** Freeze contracts and compatibility/checkpoint semantics in `nexusflow/modules/05-durable-runtime/contracts/`. Prerequisite: shared contract freeze in [contracts](../../contracts.md). Maps RT-FR-01–07, RT-A1–A7. Coordinator reviews dependent ports before parallel work.
- [ ] **RT-T02 [P]** Implement deterministic interpreter in `nexusflow/modules/05-durable-runtime/core/`. Prerequisite RT-T01. Maps RT-FR-02–04/06, RT-A1/A5/A7. Own no adapters/tests.
- [ ] **RT-T03 [P]** Implement Temporal bridge, start adapters, deduplication, version binding, checkpoint and projections in `nexusflow/modules/05-durable-runtime/adapters/`. Prerequisite RT-T01; use frozen core fake until RT-T02 integration. Maps RT-FR-01/03–07, RT-A2–A6.
- [ ] **RT-T04 [P]** Author independent contract/acceptance/replay fixtures in `nexusflow/modules/05-durable-runtime/tests/`. Prerequisite RT-T01. Maps RT-A1–A7; include negative authorization and recovery, fake ports and virtual time. Own no provider code.
- [ ] **RT-T05** Integrate and verify core/adapters/tests; update `nexusflow/modules/05-durable-runtime/verification.md`. Prerequisites RT-T02–04. Serial integration owner may edit runtime-owned paths only. Pass RT-A1–A7 and applicable lint; record commands/results and publish contract compatibility evidence.

NF-T004 is implemented under nexusflow/modules/durable_runtime; see [proof evidence](../../implementation-runtime-proof.md). Broader RT-T01–05 remain open. User authorization supersedes the original planning-only wording for this increment.
