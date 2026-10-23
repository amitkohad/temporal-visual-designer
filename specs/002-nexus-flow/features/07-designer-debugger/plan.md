# Designer debugger implementation plan

Own `nexusflow/modules/07-designer-debugger/` and `nexusflow/modules/07-designer-debugger/tests/`. Integrate into the Designer through an exported panel contract; do not edit its canvas core. Follow [shared contracts](../../contracts.md). Freeze against runtime debug gate/event and Designer snapshot ports; fake providers permit independent development.

## Parallel lanes and dependencies

1. Contract owner freezes command state machine, revision conflict semantics, parallel step selection, expiry, payload redaction and event reconciliation. Runtime owner approves gate semantics; shared schema changes require coordinator review.
2. Backend lane owns module `service/`: Dev-scoped sessions, command receipts, authorization, bounded inspections, expiry and runtime adapter.
3. UI lane owns module `ui/`: debug panel, canvas overlay adapter, breakpoints, branch selection and variable inspector against frozen mocks.
4. Verification lane owns `nexusflow/modules/07-designer-debugger/tests/`: fake runtime state machine, contract tests, UI flows and recovery fixtures. No production code edits.
5. Integrate serially with runtime and Designer providers; check actual durable gate semantics and continuation before accepting UI behavior.

## Verification

Exercise DB-A1–A7 with deterministic event fixtures, explicit barriers and virtual timer/expiry time. Inject command resend, dropped event cursor, permission loss and worker restart. Verify redaction on network payloads and logs, not only rendered cells. Real connector tests use disposable Dev fixtures; default tests mock side effects. Follow repository test guidance; Application tests use the commands and integration conventions approved during stack setup. Run applicable module checks and the application lint command selected during stack setup after code changes. No implementation is part of this plan.

## Experience design dependency

UI implementation follows [ux-design.md](../../ux-design.md) and the approved frame/component references in [design-handoff.md](../../design-handoff.md). NF-T020 is an additional prerequisite for this feature's UI lane; backend and fake-contract tests retain their existing gates. Verify relevant UX-A scenarios in addition to local functional acceptance.
