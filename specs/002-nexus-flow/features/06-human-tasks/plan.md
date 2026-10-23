# Human task implementation plan

Own `nexusflow/modules/06-human-tasks/` and `nexusflow/modules/06-human-tasks/tests/`; no shared-service or Designer edits. Follow [shared contracts](../../contracts.md). Consume frozen identity, forms, execution and connector contracts with local fakes. The provider implementations need not be complete to build or test this feature.

## Parallel execution

1. Contract owner freezes task transitions, rule semantics, assignment snapshot policy, action conflicts, outbox delivery and attachment policies in module `contracts/`.
2. Backend lane owns module `service/`: tasks, policy evaluation, authorization, timers, outbox and storage adapters behind frozen ports.
3. UI lane owns module `ui/`: inbox, task form host, decisions, delegation and progress against mock APIs. It consumes the form renderer contract and does not own form authoring.
4. Verification lane owns `nexusflow/modules/06-human-tasks/tests/`: deterministic fixtures, contract tests, browser scenarios and restart/concurrency fault cases. It may run concurrently with backend/UI after contracts freeze.
5. Serial integration binds runtime, identity and notification providers, runs isolated acceptance suites and records evidence. Cross-module fixes go back to the module owner.

## Verification and limits

Use fake clock and synchronization barriers for deadline/action races. Inject crashes after decision commit, before send and after runtime acknowledgement. Check organization/environment filtering on every action and attachment route. Include empty groups and multiple-group membership. Entra outages yield a visible retryable creation failure without permissive fallback. Read repository testing guide before future tests; application test and integration commands are selected during stack setup. After code changes run appropriate module checks and the application lint command selected during stack setup; no code or tests are generated now.

## Experience design dependency

UI implementation follows [ux-design.md](../../ux-design.md) and the approved frame/component references in [design-handoff.md](../../design-handoff.md). NF-T020 is an additional prerequisite for this feature's UI lane; backend and fake-contract tests retain their existing gates. Verify relevant UX-A scenarios in addition to local functional acceptance.
