# Feature 09: Whole-project promotion

## Requirements
Shared contracts: [contracts.md](../../contracts.md). Admin manages immutable whole-project releases.

- F09-FR01: Export workflows, forms, exact activity artifacts/dependencies, configuration declarations, and checksums as one project package; exclude secrets and environment bindings.
- F09-FR02: Validate archive safety, integrity, format/runtime compatibility, executable dependency availability/trust, target bindings, and authorization without executing imported code.
- F09-FR03: Separate import, validation, and activation. Business users may promote Dev/UAT; production support controls Prod. Enforce organization/environment permissions server-side.
- F09-FR04: Activate all project workflows atomically with expected-revision concurrency checks. Running executions retain their original release, configuration revision, and compatible runtime/activities.
- F09-FR05: Rollback selects a retained compatible release for new starts. Preserve dependencies needed by running executions. Rollback does not reverse external effects.
- F09-FR06: Offer OPTIONAL change-request numbers, notes, and approval uploads for Prod. No change-request integration or approval workflow. Missing evidence never blocks authorized activation.
- F09-FR07: Always audit actor, target, checksum, release, prior activation, operation ID, time, and outcome. Evidence retrieval requires authorization and safe attachment handling.
- F09-FR08: Import and activation retries are idempotent by operation ID; interrupted operations expose recoverable status with no partial activation.

## Acceptance scenarios
- F09-A01 (positive, FR01–03): Given a complete Dev package, when a business user imports into UAT with valid bindings, then validation passes and import alone leaves activation unchanged.
- F09-A02 (negative, FR02/03): Given tampered content, unsafe paths, missing executable dependencies, or a business user targeting Prod, when promotion is attempted, then no code executes or activation changes and the reason is recorded.
- F09-A03 (positive, FR04/05): Given running release A, when support activates B then rolls back, then new starts resolve B then A project-wide while existing runs stay pinned.
- F09-A04 (positive/negative, FR06/07): Given Prod activation, when evidence is absent, then activation succeeds with audit; supplied evidence is retrievable only by authorized users.
- F09-A05 (recovery, FR04/08): Given concurrent activation or failure after commit before response, when requests retry, then one expected-revision transition wins, status reconciles, and no mixed project release appears.
- F09-A06 (negative, FR05): Given an incompatible rollback target, when rollback is attempted, then active release remains unchanged and missing dependencies are reported.

## Data, interfaces, independent success
Own ImportRecord, ValidationReport, ActivationRecord, OptionalDeploymentEvidence, ActivationStatus. Consume ProjectReleaseManifest, ArtifactStore, ActivityRegistry, binding resolver, authorization, and execution release resolver ports. Evidence is separate from package content. Independent tests use fake stores/registry/identity/runtime; all six scenarios must pass without Designer, Azure, or live Temporal. Zero secret fixtures in exports; zero automatic code execution on import; concurrent starts never resolve mixed project releases.
