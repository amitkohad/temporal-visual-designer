# Feature 08 implementation plan

## Contract gate
Agree shared envelopes and error/idempotency semantics in [contracts.md](../../contracts.md) before implementation. Proposed owned interfaces live in `nexusflow/modules/08-connectors/contracts/`. Any shared-contract change requires a reviewed compatibility update; agents must not independently edit shared contracts.

## Ownership and parallel lanes
- Backend: `nexusflow/modules/08-connectors/backend/` owns descriptor registry, connection validation, secret resolution boundary, and provider adapters.
- UI: `nexusflow/modules/08-connectors/ui/` owns configuration forms and connection-selection presentation against frozen schemas and fake APIs.
- Tests: `nexusflow/modules/08-connectors/tests/` owns fake providers, contract fixtures, scenario tests, and secret-leak assertions.
- Integration: `nexusflow/modules/08-connectors/integration/` wires ports to runtime, authorization, connections, and Designer after all lanes pass.

Backend, UI, and test lanes can run concurrently after contract freeze with no shared-file writes. Backend adapter tasks touching common dispatch code run sequentially. Other features consume descriptors via ports and can use stubs before this feature exists.

## Verification
Contract fixtures cover all descriptors and error classes. Adapter tests exercise cancellation, redirection boundaries, retry identity, and ambiguous outcomes with controlled synchronization rather than sleeps. UI tests cover actionable validation and redaction. Integration adds one real disposable HTTP endpoint; external email delivery is not required for independent acceptance. Future language/toolchain commands are selected when the application stack is ratified; Application tests follow the commands and integration conventions selected during stack setup. Run applicable lint after implementation, not for these documents.

## Experience design dependency

UI implementation follows [ux-design.md](../../ux-design.md) and the approved frame/component references in [design-handoff.md](../../design-handoff.md). NF-T020 is an additional prerequisite for this feature's UI lane; backend and fake-contract tests retain their existing gates. Verify relevant UX-A scenarios in addition to local functional acceptance.
