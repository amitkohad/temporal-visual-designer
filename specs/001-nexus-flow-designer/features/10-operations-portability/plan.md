# Feature 10 plan

Freeze placement, admission, lifecycle, and adapter contracts against [contracts.md](../../contracts.md). Entra verifies identity; application authorization consumes a verified-principal contract. This feature owns application behavior only.

## Parallel ownership
- `nexusflow/modules/10-operations-portability/contracts/`: schemas, contract lead before freeze.
- `nexusflow/modules/10-operations-portability/backend/`: routing guards, admission, health, drain, diagnostics.
- `nexusflow/modules/10-operations-portability/adapters/`: provider conformance/configuration bindings; no infrastructure manifests.
- `nexusflow/modules/10-operations-portability/tests/`: isolation, admission, recovery, redaction, benchmark methodology.
- `nexusflow/modules/10-operations-portability/integration/`: runtime composition and verification records.

Backend, adapters, and tests run independently after freeze. Domain backend cannot import concrete cloud adapters. Integration waits for runtime/identity/registry/task ports; fakes enable earlier acceptance. Review dependencies before adding libraries. Use deterministic drain/restart coordination; no timing races. Production thresholds remain unresolved until sizing is supplied. Commands are selected with the application stack; Application tests use the commands and integration conventions approved during stack setup. Infrastructure work requires a separate reviewed deployment plan for this project.
