# Operations portability first slice (F10-T01a + fake-port)

Branch: `feature/f10-ops-portability-readiness-206b`. Decision:
[ADR 0072](../../docs/decisions/0072-operations-portability-contracts.md).

## Bounded implementation scope

Freeze PlacementResolver, AdmissionPolicy, ReadinessReport, DrainStatus and related
errors under `nexusflow/modules/operations_portability/`. Map F10-A01–A05 at the
contract layer with no provisioning APIs.

Ship a minimal fake-port backend (`FakePlacementResolver`, `FakeAdmissionPolicy`,
`FakeLifecycle`, `OperationsPortabilityService`) that:

- resolves trusted scope→namespace/queue/pool and rejects caller overrides
- enforces independent per-scope admission budgets with explicit reject codes
- reports readiness/drain status without secrets
- pins accepted work across drain/restart and diagnoses incompatible placement changes

No Azure SDK, Key Vault, App Insights, infrastructure manifests or cluster migration.

## Verification

```sh
uv sync --locked
uv run pytest -q nexusflow/tests/operations_portability/ nexusflow/tests/readiness/
```

## Remaining feature acceptance

Nested **F10-T01a** is checked for this contract freeze. Parents **F10-T01**–**F10-T06**
stay open. Full routing/lifecycle/diagnostics backend (F10-T02), provider adapters
(F10-T03), complete suites/worksheet (F10-T04), integration (F10-T05) and production
verification.md gate (F10-T06) remain outstanding.
