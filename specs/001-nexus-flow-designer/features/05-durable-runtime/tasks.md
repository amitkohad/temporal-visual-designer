# Durable runtime tasks

Program gate: NF-T003 and later implementation authorization are required before any task below. Shared contracts take precedence; freeze feature-local contracts before parallel lanes. Integration depends on actual providers as listed in the program plan.

`[P]` permits concurrent execution only after listed prerequisites. Paths are future ownership boundaries. No code is requested now.

- [ ] **RT-T01** Freeze contracts and compatibility/checkpoint semantics in `nexusflow/modules/05-durable-runtime/contracts/`. Prerequisite: shared contract freeze in [contracts](../../contracts.md). Maps RT-FR-01–07, RT-A1–A7. Coordinator reviews dependent ports before parallel work.
  - [x] **RT-T01a** Freeze bounded manual/API start request/receipt, authorization, atomic active-release snapshot and idempotent dispatcher ports (ADR 0009; `durable_runtime/starts.py`).
  - [x] **RT-T01c** Freeze async proof-runtime dispatch and permanent journal/reconciliation semantics, including ambiguous claims and remote retention limitations (ADR 0010).
  - [ ] **RT-T01b** Freeze remaining control flow, checkpoint, event, webhook, schedule and full compatibility contracts.
- [ ] **RT-T02 [P]** Implement deterministic interpreter in `nexusflow/modules/05-durable-runtime/core/`. Prerequisite RT-T01. Maps RT-FR-02–04/06, RT-A1/A5/A7. Own no adapters/tests.
- [ ] **RT-T03 [P]** Implement Temporal bridge, start adapters, deduplication, version binding, checkpoint and projections in `nexusflow/modules/05-durable-runtime/adapters/`. Prerequisite RT-T01; use frozen core fake until RT-T02 integration. Maps RT-FR-01/03–07, RT-A2–A6.
  - [x] **RT-T03a** Implement isolated SQLite start intent reservation, scoped input deduplication/conflicts, preflight and immutable release/configuration binding with retry recovery through required ports.
  - [x] **RT-T03c** Implement async start service and actual proof-runtime Temporal dispatcher with permanent SQLite receipts, exact plan/route conflicts and conservative history reconciliation.
  - [ ] **RT-T03b** Implement actual Temporal/identity/activation adapters, automatic recovery orchestration, webhook/form/schedule starts, checkpoints and projections.
- [ ] **RT-T04 [P]** Author independent contract/acceptance/replay fixtures in `nexusflow/modules/05-durable-runtime/tests/`. Prerequisite RT-T01. Maps RT-A1–A7; include negative authorization and recovery, fake ports and virtual time. Own no provider code.
  - [x] **RT-T04a** Verify isolated start reliability with real SQLite, independent provider fakes, concurrency, conflicting input, lost acknowledgements, restart, reauthorization and scope isolation (`tests/runtime/test_starts.py`).
  - [x] **RT-T04c** Verify dispatch security/recovery/cancellation and concurrent journal claims with SDK fakes; verify actual Temporal completed-run lost-ack recovery and mismatched-plan rejection, retaining existing replay/restart suite.
  - [ ] **RT-T04b** Complete full RT-A1–A7, actual Temporal start/replay/retention integration and remaining failure/continuation fixtures.
- [ ] **RT-T05** Integrate and verify core/adapters/tests; update `nexusflow/modules/05-durable-runtime/verification.md`. Prerequisites RT-T02–04. Serial integration owner may edit runtime-owned paths only. Pass RT-A1–A7 and applicable lint; record commands/results and publish contract compatibility evidence.

NF-T004 is implemented under nexusflow/modules/durable_runtime; see [proof evidence](../../implementation-runtime-proof.md). Broader RT-T01–05 remain open. User authorization supersedes the original planning-only wording for this increment.

Start-reliability evidence and remaining scope: [implementation record](../../implementation-durable-starts.md). The RT-T01a start subset gates RT-T03a/RT-T04a only; it does not unblock other adapters that require the complete contract freeze.

Actual proof-runtime dispatch evidence: [implementation record](../../implementation-temporal-start-dispatch.md). RT-T01c gates RT-T03c/RT-T04c only. RT-T03b/T04b remain open for the remaining actual providers and complete runtime acceptance; the Temporal start adapter subset is checked independently above.
