# Readiness measured results (NF-T017a)

Date: 2026-11-17. Boundary: fake-port operations under
`nexusflow/modules/operations_portability/` plus critical-path tests in this
directory. No production cluster, Azure provider or load generator was used.

## Measurements recorded this slice

| Observation | Result | Conditions |
|---|---|---|
| Cross-org resume of foreign work_id | `not_found` (denied) | In-memory fake scopes `acme/dev` vs `globex/dev` |
| Cross-env placement within one org | independent namespace/queue; foreign pin `not_found` | `acme/dev` vs `acme/uat` |
| Drain rejects new admissions | `rejected_draining` while accepted IDs retained | Single-process fake admission table |
| Restart restores pinned work IDs | pins + in-flight rebuilt; phase returns `accepting` | Same process; no Temporal server |
| Placement replacement vs pinned run | resume keeps original pin; `diagnose_migration` → `incompatible_dependency` | Generation bump + queue rename |

## Unresolved sizing / recovery targets (SC-06)

Do **not** invent numbers. The following remain **unresolved** pending approved
product/ops targets before production readiness acceptance:

- Sustained and peak start throughput per organization/environment
- Maximum concurrent in-flight executions / waiting tasks / artifact storage quotas
- Multi-tenant noisy-neighbor isolation SLOs under shared worker pools
- Availability objective for control plane vs worker data plane
- RTO and RPO for control metadata, artifacts, task state and Temporal namespaces
- Drain deadline / forced-cancel policy under incomplete worker pools
- Accessibility certification evidence for operator surfaces (out of this fake-port slice)

Production gate for parent **NF-T017** / **SC-06** stays open until these targets
are supplied and re-measured against disposable or approved environments.
