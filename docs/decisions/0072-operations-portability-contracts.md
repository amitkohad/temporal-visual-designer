# ADR 0072: Operations portability contracts (F10-T01a)

Status: accepted for the F10-T01a contract freeze and fake-port first slice.
Parents F10-T01–T06 and NF-T017 remain open.

## Decision

Freeze feature-local ports under `nexusflow/modules/operations_portability/`:

- `PlacementResolver` / `PlacementTarget` / `PlacementOverride`
- `AdmissionPolicy` / `AdmissionBudget` / `AdmissionDecision`
- `ReadinessReport` / `ReadinessComponent`
- `DrainStatus` / `PinnedWork`

Shared C10 semantics apply: placement maps trusted organization/environment scope to
Temporal namespace, task queue and worker pool. UI/API callers cannot supply arbitrary
destinations. Quotas are per-scope. Telemetry correlation stays bounded; this slice
does not emit metric series.

### Mapping

| Scenario | Contract behavior |
|---|---|
| F10-A01 | `resolve(..., override=...)` rejects any caller-supplied namespace/queue/pool |
| F10-A02 | Independent `AdmissionBudget` per scope; exhausted scopes return `rejected_budget` |
| F10-A03 | `PinnedWork` + drain/restart restore in-flight IDs without dropping accepted work |
| F10-A04 | Domain types/protocols import no Azure SDK; fake-port implements the ports |
| F10-A05 | `diagnose_migration` reports `incompatible_dependency` instead of silent remapping |

### Fake-port boundary

`OperationsPortabilityService` composes in-memory placement, admission and lifecycle
adapters for critical-path tests. It registers scopes, admits/pins work, reports
readiness/drain status and preserves pins across restart. It does **not** provision
namespaces, deploy workers, bind Key Vault/App Insights or migrate running clusters.

## Non-claims

No Azure resources, infrastructure manifests, dedicated placement pools, production
admission gateways, accessibility certification, capacity SLOs, RTO/RPO targets or
full F10-T03–T06 provider conformance. SC-06 numeric targets remain unresolved until
supplied; readiness notes must record measurements and gaps without inventing them.
