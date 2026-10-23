# Feature 09 plan

Freeze manifest, artifact, compatibility, authorization, activation revision, and audit schemas against [contracts.md](../../contracts.md). Evidence is optional in API, UI, and validation. Stubs permit independent testing before registry/runtime delivery.

## Disjoint ownership after freeze
- `nexusflow/modules/09-package-promotion/contracts/`: feature schemas; contract lead only.
- `nexusflow/modules/09-package-promotion/backend/`: archive verification, import state, atomic activation, rollback, dependency retention, audit.
- `nexusflow/modules/09-package-promotion/ui/`: Admin import, validation, bindings, activation/rollback, optional evidence.
- `nexusflow/modules/09-package-promotion/tests/`: archive safety, permission, concurrency, recovery fixtures.
- `nexusflow/modules/09-package-promotion/integration/`: real-port assembly after lanes pass.

Backend, UI, and tests run concurrently against frozen fixtures. Integration waits for versioning, registry, identity, and runtime ports. Activation is a durable atomic transition; evidence upload cannot create partial activation. Deterministic failure tests cover before/after commit and expected-revision contention. Full verification promotes a multi-workflow fixture across environments while older executions run. Commands are chosen with application stack selection; Application tests use the commands and integration conventions approved during stack setup. No deployment infrastructure changes.

## Experience design dependency

UI implementation follows [ux-design.md](../../ux-design.md) and the approved frame/component references in [design-handoff.md](../../design-handoff.md). NF-T020 is an additional prerequisite for this feature's UI lane; backend and fake-contract tests retain their existing gates. Verify relevant UX-A scenarios in addition to local functional acceptance.
